"""Merkezi kimlik doğrulama, yetki ve kiracı bağımlılıkları (Faz 0.3 / 0.4 / 0.7).

Kurallar:
- ``buro_id`` YALNIZCA doğrulanmış kullanıcıdan (token → veritabanı) gelir.
  ``X-Buro-ID`` header'ı yok sayılır.
- ``X-Mukellef-ID`` her istekte doğrulanır: mükellef kullanıcının bürosuna ait
  olmalı ve kullanıcı ona erişebilmelidir (admin/müşavir tüm büro, diğerleri atama).
- Yetkisiz mükellef erişimi 404 döner; kaydın varlığı sızdırılmaz.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import APPROVER_ROLES, BURO_WIDE_ROLES, WRITE_ROLES, normalize_role
from app.auth.security import decode_token
from app.core.db import get_session
from app.core.models import KullaniciMukellef, Mukellef, User

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized("Oturum açmanız gerekiyor.")

    payload = decode_token(authorization.split(" ", 1)[1].strip())
    if payload is None:
        raise _unauthorized("Geçersiz veya süresi dolmuş oturum.")

    try:
        user_id = UUID(str(payload["sub"]))
        token_buro_id = UUID(str(payload["buro_id"]))
    except (KeyError, ValueError):
        raise _unauthorized("Geçersiz veya süresi dolmuş oturum.") from None

    user = await session.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or user.buro_id != token_buro_id
        or user.token_version != payload.get("tv")
        or normalize_role(user.rol) is None
    ):
        raise _unauthorized("Geçersiz veya süresi dolmuş oturum.")

    return user


def user_role(user: User) -> str:
    role = normalize_role(user.rol)
    if role is None:  # get_current_user zaten eler; savunma amaçlı.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Yetkiniz yok.")
    return role


def require_roles(*roles: str) -> Callable[..., Awaitable[User]]:
    allowed = frozenset(roles)

    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user_role(user) not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bu işlem için yetkiniz yok.",
            )
        return user

    return dependency


require_admin = require_roles("admin")
require_approver = require_roles(*APPROVER_ROLES)


async def enforce_default_policy(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Router seviyesinde varsayılan politika: okuma tüm rollere, yazma mükellef hariç."""
    if request.method not in _SAFE_METHODS and user_role(user) not in WRITE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Salt okuma yetkiniz var; bu işlemi yapamazsınız.",
        )
    return user


async def can_access_mukellef(session: AsyncSession, user: User, mukellef_id: UUID) -> bool:
    mukellef = await session.scalar(
        select(Mukellef.id).where(Mukellef.id == mukellef_id, Mukellef.buro_id == user.buro_id)
    )
    if mukellef is None:
        return False
    if user_role(user) in BURO_WIDE_ROLES:
        return True
    assignment = await session.scalar(
        select(KullaniciMukellef.id).where(
            KullaniciMukellef.user_id == user.id,
            KullaniciMukellef.mukellef_id == mukellef_id,
            KullaniciMukellef.buro_id == user.buro_id,
        )
    )
    return assignment is not None


async def tenant_context(
    x_mukellef_id: UUID | None = Header(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> tuple[UUID, UUID]:
    """(buro_id, mukellef_id) döner. buro_id token'daki kullanıcıdan gelir."""
    if x_mukellef_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Mukellef-ID header'ı zorunludur.",
        )
    if not await can_access_mukellef(session, user, x_mukellef_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mükellef bulunamadı.")
    return user.buro_id, x_mukellef_id


async def buro_context(user: User = Depends(get_current_user)) -> UUID:
    """Yalnızca büro kapsamındaki kaynaklar (ör. vergi parametreleri) için."""
    return user.buro_id
