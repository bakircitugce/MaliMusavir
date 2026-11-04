from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.auth.security import create_token, decode_token
from app.auth.service import (
    authenticate,
    change_user_password,
    create_user,
    ensure_default_accounts,
    list_users,
    toggle_user_active,
)
from app.core.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(..., description="E-posta adresi veya kullanıcı adı (örn: admin@maliasistan.com veya admin)")
    password: str = Field(..., description="Kullanıcı şifresi")


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    buro_id: UUID
    email: str
    ad_soyad: str
    rol: str
    is_active: bool


class LoginResponse(BaseModel):
    success: bool
    token: str
    user: UserResponse


class CreateUserRequest(BaseModel):
    email: str = Field(..., min_length=3)
    ad_soyad: str = Field(..., min_length=2)
    password: str = Field(..., min_length=4)
    rol: str = Field("kullanici", pattern="^(yonetici|kullanici|stajyer)$")


class ChangePasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=4)


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> User:
    """Dependency that extracts and validates user from Authorization: Bearer <token>."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Oturum açmanız gerekiyor. Lütfen giriş yapın.",
        )

    token = authorization.split(" ", 1)[1]
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz veya süresi dolmuş oturum anahtarı.",
        )

    try:
        user_id = UUID(payload["sub"])
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Geçersiz kullanıcı kimliği.")

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Kullanıcı bulunamadı veya pasif.")

    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency requiring user to have 'yonetici' role."""
    if current_user.rol != "yonetici":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu işlem için Yönetici yetkisi gerekmektedir.",
        )
    return current_user


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)) -> LoginResponse:
    # Ensure baseline accounts exist
    await ensure_default_accounts(session)

    user = await authenticate(session, req.email, req.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Hatalı e-posta veya şifre. Lütfen bilgilerinizi kontrol edin.",
        )

    token = create_token(
        user_id=str(user.id),
        email=user.email,
        rol=user.rol,
        buro_id=str(user.buro_id),
    )

    return LoginResponse(
        success=True,
        token=token,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("/logout")
async def logout() -> dict[str, str]:
    return {"status": "ok", "message": "Başarıyla çıkış yapıldı."}


@router.get("/users", response_model=list[UserResponse])
async def get_users(
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> list[UserResponse]:
    """List all bureau users. Only accessible by admins."""
    users = await list_users(session, buro_id=admin.buro_id)
    return [UserResponse.model_validate(u) for u in users]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def add_new_user(
    req: CreateUserRequest,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> UserResponse:
    """Create a new user with email, name, role, and password. Only accessible by admins."""
    try:
        new_u = await create_user(
            session=session,
            buro_id=admin.buro_id,
            email=req.email,
            ad_soyad=req.ad_soyad,
            password=req.password,
            rol=req.rol,
        )
        return UserResponse.model_validate(new_u)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/users/{user_id}/password")
async def change_password(
    user_id: UUID,
    req: ChangePasswordRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Change user password. Admin can change any user's password; standard user can only change their own."""
    if current_user.rol != "yonetici" and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Yalnızca kendi şifrenizi değiştirebilirsiniz.",
        )

    success = await change_user_password(session, user_id, req.new_password)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kullanıcı bulunamadı.")

    return {"status": "ok", "message": "Şifre başarıyla güncellendi."}


@router.patch("/users/{user_id}/status")
async def toggle_status(
    user_id: UUID,
    is_active: bool,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
) -> dict[str, str]:
    """Toggle user active status. Admin only."""
    if admin.id == user_id and not is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Kendi yöneticilik hesabınızı pasife alamazsınız.")

    success = await toggle_user_active(session, user_id, is_active)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kullanıcı bulunamadı.")

    return {"status": "ok", "message": f"Kullanıcı durumu '{'Aktif' if is_active else 'Pasif'}' olarak güncellendi."}
