"""Yönetim komutları.

    python -m app.cli migrate [--yedek-alindi]
    python -m app.cli create-admin --email ad@buro.com --ad-soyad "Ad Soyad" --buro-unvan "X SMMM"
    python -m app.cli create-admin --email ad@buro.com --ad-soyad "Ad Soyad" --buro-id <UUID>
    python -m app.cli secure-db

Şema yalnızca `migrate` ile değişir (Alembic). Diğer komutlar şema kurmaz; şema güncel
değilse çalışmayı reddeder.

Şifre komut satırından alınmaz (shell geçmişine düşmemesi için); gizli olarak
iki kez sorulur. Otomasyon için MALIASISTAN_ADMIN_PASSWORD ortam değişkeni
kullanılabilir.
"""

import argparse
import asyncio
import getpass
import os
import sys
from uuid import UUID

from sqlalchemy import select

from app.auth.roles import normalize_role
from app.auth.service import (
    PasswordPolicyError,
    create_admin,
    disable_legacy_default_accounts,
    validate_password_strength,
)
from app.core.config import get_settings
from app.core.db import engine, session_factory
from app.core.models import User
from app.core.schema_check import SchemaNotReadyError, ensure_schema_ready, run_migrate


def _read_password() -> str:
    env_password = os.environ.get("MALIASISTAN_ADMIN_PASSWORD")
    if env_password:
        return env_password
    first = getpass.getpass("Yönetici şifresi: ")
    second = getpass.getpass("Şifre (tekrar): ")
    if first != second:
        raise SystemExit("Şifreler eşleşmiyor.")
    return first


async def _require_schema() -> None:
    try:
        await ensure_schema_ready(engine)
    except SchemaNotReadyError as exc:
        raise SystemExit(str(exc)) from exc


async def secure_db() -> None:
    """Varsayılan hesapları kapatır ve eski rolleri eşler. Şema değiştirmez."""
    await _require_schema()
    async with session_factory() as session:
        disabled = await disable_legacy_default_accounts(session)
        remapped = 0
        for user in (await session.scalars(select(User))).all():
            role = normalize_role(user.rol)
            if role and role != user.rol:
                user.rol = role
                remapped += 1
        await session.commit()
    print(f"Pasife alınan varsayılan hesaplar: {', '.join(disabled) or '-'}")
    print(f"Yeni rol modeline eşlenen kullanıcı sayısı: {remapped}")


async def _create_admin(args: argparse.Namespace) -> None:
    await _require_schema()
    password = _read_password()
    try:
        validate_password_strength(password)
    except PasswordPolicyError as exc:
        raise SystemExit(str(exc)) from exc
    async with session_factory() as session:
        try:
            user = await create_admin(
                session,
                email=args.email,
                ad_soyad=args.ad_soyad,
                password=password,
                buro_id=UUID(args.buro_id) if args.buro_id else None,
                buro_unvan=args.buro_unvan,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    print(f"Yönetici oluşturuldu: {user.email}")
    print(f"BURO_ID={user.buro_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser(
        "migrate",
        help="Veritabanını güvenle son şema sürümüne taşır (önce yedek alır; kısmi şemada "
        "değişiklik yapmaz, kurtarma planı raporlar).",
    )
    migrate.add_argument(
        "--yedek-alindi",
        action="store_true",
        help="SQLite dışındaki veritabanlarında yedeğin elle alındığını beyan eder.",
    )

    admin = sub.add_parser("create-admin", help="İlk büro yöneticisini oluşturur.")
    admin.add_argument("--email", required=True)
    admin.add_argument("--ad-soyad", required=True)
    target = admin.add_mutually_exclusive_group(required=True)
    target.add_argument("--buro-id", help="Mevcut büronun UUID'si")
    target.add_argument("--buro-unvan", help="Yeni oluşturulacak büronun unvanı")

    sub.add_parser(
        "secure-db",
        help="Varsayılan hesapları kapatır ve eski rolleri yeni modele eşler (şema değiştirmez).",
    )

    args = parser.parse_args(argv)
    if args.command == "migrate":
        return run_migrate(get_settings().database_url, assume_backup=args.yedek_alindi)
    if args.command == "create-admin":
        asyncio.run(_create_admin(args))
    elif args.command == "secure-db":
        asyncio.run(secure_db())
    return 0


if __name__ == "__main__":
    sys.exit(main())
