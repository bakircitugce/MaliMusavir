"""Testlerde kiracı + kullanıcı + token üretimi için yardımcılar."""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.security import create_token, hash_password
from app.core.models import Buro, KullaniciMukellef, Mukellef, User

TEST_PASSWORD = "GucluSifre2026!"


@dataclass
class Actor:
    user: User
    token: str

    def headers(self, mukellef_id: UUID | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if mukellef_id is not None:
            headers["X-Mukellef-ID"] = str(mukellef_id)
        return headers


async def make_buro(
    factory: async_sessionmaker[AsyncSession],
    *,
    buro_id: UUID | None = None,
    mukellef_ids: Iterable[UUID] = (),
) -> UUID:
    buro_id = buro_id or uuid4()
    async with factory() as session:
        session.add(Buro(id=buro_id, unvan=f"Büro {buro_id.hex[:6]}"))
        for index, mukellef_id in enumerate(mukellef_ids):
            session.add(
                Mukellef(
                    id=mukellef_id,
                    buro_id=buro_id,
                    vkn_tckn=f"{index:010d}",
                    unvan=f"Mükellef {mukellef_id.hex[:6]}",
                    mukellef_turu="kurumlar",
                )
            )
        await session.commit()
    return buro_id


async def make_actor(
    factory: async_sessionmaker[AsyncSession],
    buro_id: UUID,
    *,
    rol: str = "admin",
    assigned: Iterable[UUID] = (),
    email: str | None = None,
) -> Actor:
    async with factory() as session:
        user = User(
            buro_id=buro_id,
            email=email or f"{rol}-{uuid4().hex[:8]}@test.local",
            ad_soyad=f"Test {rol}",
            rol=rol,
            password_hash=hash_password(TEST_PASSWORD),
            is_active=True,
        )
        session.add(user)
        await session.flush()
        for mukellef_id in assigned:
            session.add(KullaniciMukellef(buro_id=buro_id, user_id=user.id, mukellef_id=mukellef_id))
        await session.commit()
        await session.refresh(user)
    token = create_token(
        user_id=str(user.id), buro_id=str(buro_id), rol=rol, token_version=user.token_version
    )
    return Actor(user=user, token=token)
