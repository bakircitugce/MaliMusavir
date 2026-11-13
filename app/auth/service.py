from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.roles import ALL_ROLES, BURO_WIDE_ROLES, normalize_role
from app.auth.security import dummy_verify, hash_password, needs_rehash, verify_password
from app.core.config import get_settings
from app.core.models import Buro, KullaniciMukellef, Mukellef, User

MIN_PASSWORD_LENGTH = 12

# Faz 0.2: eski sürümlerin açılışta oluşturduğu, şifresi kamuya açık hesaplar.
LEGACY_DEFAULT_EMAILS = (
    "admin@maliasistan.com",
    "muhasebe@maliasistan.com",
    "yonetici@maliasistan.local",
    "kullanici@maliasistan.local",
)


class PasswordPolicyError(ValueError):
    pass


def validate_password_strength(password: str) -> None:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"en az {MIN_PASSWORD_LENGTH} karakter")
    if not any(c.islower() for c in password):
        problems.append("küçük harf")
    if not any(c.isupper() for c in password):
        problems.append("büyük harf")
    if not any(c.isdigit() for c in password):
        problems.append("rakam")
    if problems:
        raise PasswordPolicyError("Şifre şunları içermelidir: " + ", ".join(problems) + ".")


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def is_locked(user: User, now: datetime | None = None) -> bool:
    locked_until = _as_aware(user.locked_until)
    return locked_until is not None and locked_until > (now or datetime.now(UTC))


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    """E-posta + şifre doğrular. Başarısız denemeleri sayar ve hesabı geçici kilitler.

    Kullanıcı yok / şifre yanlış / kilitli / pasif durumlarının hepsi aynı
    sonucu (None) döndürür ve benzer maliyette çalışır.
    """
    settings = get_settings()
    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        dummy_verify(password)
        return None

    now = datetime.now(UTC)
    if is_locked(user, now) or not user.is_active:
        dummy_verify(password)
        return None

    if not verify_password(password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.login_max_failed_attempts:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_login_count = 0
        await session.commit()
        return None

    user.failed_login_count = 0
    user.locked_until = None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    await session.commit()
    return user


async def list_users(session: AsyncSession, buro_id: UUID) -> list[User]:
    stmt = (
        select(User)
        .where(User.buro_id == buro_id)
        .order_by(User.rol, User.created_at.desc())
    )
    return list((await session.scalars(stmt)).all())


async def get_buro_user(session: AsyncSession, buro_id: UUID, user_id: UUID) -> User | None:
    return await session.scalar(select(User).where(User.id == user_id, User.buro_id == buro_id))


async def create_user(
    session: AsyncSession,
    *,
    buro_id: UUID,
    email: str,
    ad_soyad: str,
    password: str,
    rol: str,
) -> User:
    role = normalize_role(rol)
    if role not in ALL_ROLES:
        raise ValueError("Geçersiz rol.")
    validate_password_strength(password)
    cleaned_email = email.strip().lower()
    if await session.scalar(select(User.id).where(User.email == cleaned_email)):
        raise ValueError("Bu e-posta ile kayıtlı bir kullanıcı zaten mevcut.")

    user = User(
        buro_id=buro_id,
        email=cleaned_email,
        ad_soyad=ad_soyad.strip(),
        rol=role,
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def set_password(session: AsyncSession, user: User, new_password: str) -> None:
    """Şifreyi değiştirir ve kullanıcının tüm eski token'larını geçersiz kılar."""
    validate_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    user.token_version = (user.token_version or 0) + 1
    user.failed_login_count = 0
    user.locked_until = None
    await session.commit()


async def revoke_tokens(session: AsyncSession, user: User) -> None:
    user.token_version = (user.token_version or 0) + 1
    await session.commit()


async def set_active(session: AsyncSession, user: User, is_active: bool) -> None:
    user.is_active = is_active
    if not is_active:
        user.token_version = (user.token_version or 0) + 1
    await session.commit()


async def list_accessible_mukellefler(session: AsyncSession, user: User) -> list[Mukellef]:
    stmt = select(Mukellef).where(Mukellef.buro_id == user.buro_id)
    if normalize_role(user.rol) not in BURO_WIDE_ROLES:
        stmt = stmt.join(
            KullaniciMukellef,
            (KullaniciMukellef.mukellef_id == Mukellef.id)
            & (KullaniciMukellef.user_id == user.id),
        )
    return list((await session.scalars(stmt.order_by(Mukellef.unvan))).all())


async def list_assignments(session: AsyncSession, buro_id: UUID, user_id: UUID) -> list[UUID]:
    rows = await session.scalars(
        select(KullaniciMukellef.mukellef_id).where(
            KullaniciMukellef.buro_id == buro_id, KullaniciMukellef.user_id == user_id
        )
    )
    return list(rows.all())


async def assign_mukellef(
    session: AsyncSession, buro_id: UUID, user: User, mukellef_id: UUID
) -> bool:
    """Mükellef aynı büroda değilse False döner. Tekrar atama idempotenttir."""
    if user.buro_id != buro_id:
        return False
    mukellef = await session.scalar(
        select(Mukellef.id).where(Mukellef.id == mukellef_id, Mukellef.buro_id == buro_id)
    )
    if mukellef is None:
        return False
    existing = await session.scalar(
        select(KullaniciMukellef.id).where(
            KullaniciMukellef.user_id == user.id, KullaniciMukellef.mukellef_id == mukellef_id
        )
    )
    if existing is None:
        session.add(KullaniciMukellef(buro_id=buro_id, user_id=user.id, mukellef_id=mukellef_id))
        await session.commit()
    return True


async def unassign_mukellef(
    session: AsyncSession, buro_id: UUID, user_id: UUID, mukellef_id: UUID
) -> bool:
    row = await session.scalar(
        select(KullaniciMukellef).where(
            KullaniciMukellef.buro_id == buro_id,
            KullaniciMukellef.user_id == user_id,
            KullaniciMukellef.mukellef_id == mukellef_id,
        )
    )
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def create_admin(
    session: AsyncSession,
    *,
    email: str,
    ad_soyad: str,
    password: str,
    buro_id: UUID | None = None,
    buro_unvan: str | None = None,
) -> User:
    """CLI için ilk yöneticiyi oluşturur (Faz 0.2). Mevcut büro ya da yeni büro."""
    if buro_id is None:
        if not buro_unvan:
            raise ValueError("Büro kimliği veya yeni büro unvanı verilmelidir.")
        buro = Buro(unvan=buro_unvan.strip())
        session.add(buro)
        await session.flush()
        buro_id = buro.id
    elif await session.get(Buro, buro_id) is None:
        raise ValueError("Büro bulunamadı.")
    return await create_user(
        session, buro_id=buro_id, email=email, ad_soyad=ad_soyad, password=password, rol="admin"
    )


async def disable_legacy_default_accounts(session: AsyncSession) -> list[str]:
    """Eski varsayılan hesapları pasife alır, şifrelerini siler ve token'larını iptal eder."""
    users = (
        await session.scalars(select(User).where(User.email.in_(LEGACY_DEFAULT_EMAILS)))
    ).all()
    for user in users:
        user.is_active = False
        user.password_hash = None
        user.token_version = (user.token_version or 0) + 1
    await session.commit()
    return [user.email for user in users]
