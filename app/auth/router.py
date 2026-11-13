from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.auth.deps import get_current_user, require_admin, user_role
from app.auth.ratelimit import SlidingWindowLimiter
from app.auth.roles import ALL_ROLES, normalize_role
from app.auth.security import create_token, verify_password
from app.auth.service import (
    PasswordPolicyError,
    assign_mukellef,
    authenticate,
    create_user,
    get_buro_user,
    list_accessible_mukellefler,
    list_assignments,
    list_users,
    revoke_tokens,
    set_active,
    set_password,
    unassign_mukellef,
)
from app.core.config import get_settings
from app.core.db import get_session
from app.core.models import AuditLog, User

# /auth/login dışındaki tüm uçlar kendi bağımlılıklarıyla kimlik ister.
router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_LOGIN = "E-posta veya şifre hatalı."
login_limiter = SlidingWindowLimiter(get_settings().login_rate_limit_per_minute, 60.0)
# Tek IP'den çok sayıda farklı e-posta denenmesine (password spraying) karşı.
login_ip_limiter = SlidingWindowLimiter(get_settings().login_rate_limit_per_minute * 3, 60.0)

_ROLE_PATTERN = "^(" + "|".join(sorted(ALL_ROLES)) + ")$"


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=256)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    buro_id: UUID
    email: str
    ad_soyad: str
    rol: str
    is_active: bool

    @classmethod
    def of(cls, user: User) -> "UserResponse":
        response = cls.model_validate(user)
        response.rol = normalize_role(user.rol) or user.rol
        return response


class LoginResponse(BaseModel):
    success: bool
    token: str
    token_type: str = "bearer"
    user: UserResponse


class CreateUserRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    ad_soyad: str = Field(..., min_length=2, max_length=200)
    password: str = Field(..., min_length=1, max_length=256)
    rol: str = Field("personel", pattern=_ROLE_PATTERN)


class ChangePasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=1, max_length=256)
    # Kullanıcı kendi şifresini değiştirirken zorunludur (çalınan token ile ele geçirmeye karşı).
    current_password: str | None = Field(default=None, max_length=256)


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    user_id: UUID | None
    mukellef_id: UUID | None
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    details: dict


class MukellefSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    unvan: str
    vkn_tckn: str


def _client_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}|{email.strip().lower()}"


@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LoginResponse:
    host = request.client.host if request.client else "unknown"
    if not login_ip_limiter.hit(host) or not login_limiter.hit(_client_key(request, req.email)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Çok fazla giriş denemesi. Lütfen biraz sonra tekrar deneyin.",
        )

    user = await authenticate(session, req.email, req.password)
    if user is None or normalize_role(user.rol) is None:
        await audit.record(
            session, "auth.login", request=request, outcome="failure",
            details={"email": req.email.strip().lower()[:320]},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_LOGIN)
    await audit.record(session, "auth.login", request=request, user=user)

    token = create_token(
        user_id=str(user.id),
        buro_id=str(user.buro_id),
        rol=user_role(user),
        token_version=user.token_version,
    )
    return LoginResponse(success=True, token=token, user=UserResponse.of(user))


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.of(current_user)


@router.get("/me/mukellefler", response_model=list[MukellefSummary])
async def get_my_mukellefler(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MukellefSummary]:
    mukellefler = await list_accessible_mukellefler(session, current_user)
    return [MukellefSummary.model_validate(m) for m in mukellefler]


@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Kullanıcının tüm aktif token'larını iptal eder."""
    await revoke_tokens(session, current_user)
    await audit.record(session, "auth.logout", request=request, user=current_user)
    return {"status": "ok", "message": "Çıkış yapıldı."}


@router.get("/users", response_model=list[UserResponse])
async def get_users(
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> list[UserResponse]:
    return [UserResponse.of(u) for u in await list_users(session, admin.buro_id)]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def add_new_user(
    req: CreateUserRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> UserResponse:
    try:
        new_user = await create_user(
            session,
            buro_id=admin.buro_id,
            email=req.email,
            ad_soyad=req.ad_soyad,
            password=req.password,
            rol=req.rol,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await audit.record(
        session, "user.create", request=request, user=admin, target_type="user",
        target_id=new_user.id, details={"email": new_user.email, "rol": new_user.rol},
    )
    return UserResponse.of(new_user)


@router.post("/users/{user_id}/password")
async def change_password(
    user_id: UUID,
    req: ChangePasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Admin bürosundaki herkesin, diğer kullanıcılar yalnızca kendi şifresini değiştirir."""
    if current_user.id != user_id and user_role(current_user) != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kullanıcı bulunamadı.")
    target = await get_buro_user(session, current_user.buro_id, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kullanıcı bulunamadı.")
    if target.id == current_user.id and not verify_password(
        req.current_password or "", current_user.password_hash
    ):
        await audit.record(
            session, "user.password_change", request=request, user=current_user,
            target_type="user", target_id=target.id, outcome="failure",
            details={"reason": "current_password"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Mevcut şifre hatalı."
        )
    try:
        await set_password(session, target, req.new_password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await audit.record(
        session, "user.password_change", request=request, user=current_user,
        target_type="user", target_id=target.id,
    )
    return {"status": "ok", "message": "Şifre güncellendi. Tüm oturumlar kapatıldı."}


@router.patch("/users/{user_id}/status")
async def toggle_status(
    user_id: UUID,
    is_active: bool,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> dict[str, str]:
    if admin.id == user_id and not is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kendi yönetici hesabınızı pasife alamazsınız.",
        )
    target = await get_buro_user(session, admin.buro_id, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kullanıcı bulunamadı.")
    await set_active(session, target, is_active)
    await audit.record(
        session, "user.status_change", request=request, user=admin, target_type="user",
        target_id=target.id, details={"is_active": is_active},
    )
    return {"status": "ok", "message": f"Kullanıcı {'aktif' if is_active else 'pasif'} yapıldı."}


@router.get("/users/{user_id}/mukellefler", response_model=list[UUID])
async def get_user_assignments(
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> list[UUID]:
    if await get_buro_user(session, admin.buro_id, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kullanıcı bulunamadı.")
    return await list_assignments(session, admin.buro_id, user_id)


@router.put("/users/{user_id}/mukellefler/{mukellef_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_user_assignment(
    user_id: UUID,
    mukellef_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> None:
    target = await get_buro_user(session, admin.buro_id, user_id)
    if target is None or not await assign_mukellef(session, admin.buro_id, target, mukellef_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kayıt bulunamadı.")
    await audit.record(
        session, "user.assign_mukellef", request=request, user=admin, mukellef_id=mukellef_id,
        target_type="user", target_id=user_id,
    )


@router.delete(
    "/users/{user_id}/mukellefler/{mukellef_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_user_assignment(
    user_id: UUID,
    mukellef_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> None:
    if not await unassign_mukellef(session, admin.buro_id, user_id, mukellef_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kayıt bulunamadı.")
    await audit.record(
        session, "user.unassign_mukellef", request=request, user=admin, mukellef_id=mukellef_id,
        target_type="user", target_id=user_id,
    )


@router.get("/audit-log", response_model=list[AuditLogResponse])
async def get_audit_log(
    action: str | None = Query(default=None, max_length=60),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> list[AuditLogResponse]:
    """Büronun denetim kayıtları (yalnızca admin, salt okuma)."""
    stmt = select(AuditLog).where(AuditLog.buro_id == admin.buro_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    return [AuditLogResponse.model_validate(row) for row in (await session.scalars(stmt)).all()]
