from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_admin, user_role
from app.auth.ratelimit import SlidingWindowLimiter
from app.auth.roles import ALL_ROLES, normalize_role
from app.auth.security import create_token
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
from app.core.models import User

# /auth/login dışındaki tüm uçlar kendi bağımlılıklarıyla kimlik ister.
router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_LOGIN = "E-posta veya şifre hatalı."
login_limiter = SlidingWindowLimiter(get_settings().login_rate_limit_per_minute, 60.0)

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
    if not login_limiter.hit(_client_key(request, req.email)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Çok fazla giriş denemesi. Lütfen biraz sonra tekrar deneyin.",
        )

    user = await authenticate(session, req.email, req.password)
    if user is None or normalize_role(user.rol) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_LOGIN)

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
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Kullanıcının tüm aktif token'larını iptal eder."""
    await revoke_tokens(session, current_user)
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
    return UserResponse.of(new_user)


@router.post("/users/{user_id}/password")
async def change_password(
    user_id: UUID,
    req: ChangePasswordRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Admin bürosundaki herkesin, diğer kullanıcılar yalnızca kendi şifresini değiştirir."""
    if current_user.id != user_id and user_role(current_user) != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kullanıcı bulunamadı.")
    target = await get_buro_user(session, current_user.buro_id, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kullanıcı bulunamadı.")
    try:
        await set_password(session, target, req.new_password)
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "ok", "message": "Şifre güncellendi. Tüm oturumlar kapatıldı."}


@router.patch("/users/{user_id}/status")
async def toggle_status(
    user_id: UUID,
    is_active: bool,
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
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> None:
    target = await get_buro_user(session, admin.buro_id, user_id)
    if target is None or not await assign_mukellef(session, admin.buro_id, target, mukellef_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kayıt bulunamadı.")


@router.delete(
    "/users/{user_id}/mukellefler/{mukellef_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_user_assignment(
    user_id: UUID,
    mukellef_id: UUID,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> None:
    if not await unassign_mukellef(session, admin.buro_id, user_id, mukellef_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kayıt bulunamadı.")
