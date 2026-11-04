from uuid import UUID
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Buro, Mukellef, User
from app.auth.security import hash_password, verify_password


async def ensure_default_accounts(session: AsyncSession) -> tuple[Buro, User, User]:
    """Ensure standard buro, taxpayers, admin, and user accounts exist."""
    # 1. Buro
    buro = await session.scalar(select(Buro).order_by(Buro.created_at))
    if buro is None:
        buro = Buro(unvan="Demo SMMM Bürosu")
        session.add(buro)
        await session.flush()

    # 2. Mukellef (Örnek Tavukçuluk Ltd. from screenshots)
    mukellef = await session.scalar(
        select(Mukellef).where(Mukellef.buro_id == buro.id, Mukellef.vkn_tckn == "3850123456")
    )
    if mukellef is None:
        mukellef = Mukellef(
            buro_id=buro.id,
            vkn_tckn="3850123456",
            unvan="Örnek Tavukçuluk Ltd.",
            mukellef_turu="kurumlar",
            muhasebe_sistemi="7A",
            nace_kodlari=["46.32.01"],
            defter_turu="Bilanço esası",
        )
        session.add(mukellef)
        await session.flush()

    # 3. Yönetici (Admin)
    admin_user = await session.scalar(
        select(User).where(or_(User.email == "admin@maliasistan.com", User.email == "yonetici@maliasistan.local"))
    )
    if admin_user is None:
        admin_user = User(
            buro_id=buro.id,
            email="admin@maliasistan.com",
            ad_soyad="Yönetici",
            rol="yonetici",
            password_hash=hash_password("admin123"),
            is_active=True,
        )
        session.add(admin_user)
    elif admin_user.password_hash is None:
        admin_user.password_hash = hash_password("admin123")

    # 4. Standart Kullanıcı (Muhasebeci / User)
    std_user = await session.scalar(
        select(User).where(or_(User.email == "muhasebe@maliasistan.com", User.email == "kullanici@maliasistan.local"))
    )
    if std_user is None:
        std_user = User(
            buro_id=buro.id,
            email="muhasebe@maliasistan.com",
            ad_soyad="Muhasebe Kullanıcısı",
            rol="kullanici",
            password_hash=hash_password("user123"),
            is_active=True,
        )
        session.add(std_user)
    elif std_user.password_hash is None:
        std_user.password_hash = hash_password("user123")

    await session.commit()
    return buro, admin_user, std_user


async def authenticate(
    session: AsyncSession,
    identifier: str,
    password: str,
) -> User | None:
    """Authenticate a user by email/username and password."""
    cleaned = identifier.strip().lower()

    # Allow shortcut aliases: 'admin' or 'yonetici' maps to admin, 'kullanici' to user
    conditions = [User.email.ilike(cleaned)]
    if cleaned in ("admin", "yonetici"):
        conditions.append(User.rol == "yonetici")
    if cleaned in ("kullanici", "user", "muhasebe"):
        conditions.append(User.rol == "kullanici")

    query = select(User).where(or_(*conditions))
    user = await session.scalar(query)
    if not user or not user.is_active:
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user


async def list_users(session: AsyncSession, buro_id: UUID | None = None) -> list[User]:
    """List users for a buro or all if not filtered."""
    stmt = select(User).order_by(User.rol.desc(), User.created_at.desc())
    if buro_id is not None:
        stmt = stmt.where(User.buro_id == buro_id)
    result = await session.scalars(stmt)
    return list(result.all())


async def create_user(
    session: AsyncSession,
    buro_id: UUID,
    email: str,
    ad_soyad: str,
    password: str,
    rol: str = "kullanici",
) -> User:
    """Create a new user with password hash."""
    existing = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if existing:
        raise ValueError(f"Bu e-posta ({email}) ile kayıtlı bir kullanıcı zaten mevcut.")

    user = User(
        buro_id=buro_id,
        email=email.strip().lower(),
        ad_soyad=ad_soyad.strip(),
        rol=rol.strip().lower(),
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def change_user_password(
    session: AsyncSession,
    user_id: UUID,
    new_password: str,
) -> bool:
    """Update password for an existing user."""
    user = await session.get(User, user_id)
    if not user:
        return False
    user.password_hash = hash_password(new_password)
    await session.commit()
    return True


async def toggle_user_active(
    session: AsyncSession,
    user_id: UUID,
    is_active: bool,
) -> bool:
    """Activate or deactivate user account."""
    user = await session.get(User, user_id)
    if not user:
        return False
    user.is_active = is_active
    await session.commit()
    return True
