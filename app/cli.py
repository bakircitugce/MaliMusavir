"""Yönetim komutları (Faz 0.2).

    python -m app.cli create-admin --email ad@buro.com --ad-soyad "Ad Soyad" --buro-unvan "X SMMM"
    python -m app.cli create-admin --email ad@buro.com --ad-soyad "Ad Soyad" --buro-id <UUID>
    python -m app.cli secure-db

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

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Connection

from app.auth.roles import normalize_role
from app.auth.service import (
    PasswordPolicyError,
    create_admin,
    disable_legacy_default_accounts,
    validate_password_strength,
)
from app.core.db import engine, session_factory
from app.core.models import Base, User

# Alembic gelene kadar (Faz 1) mevcut veritabanlarına eklenen Faz 0 sütunları.
_FAZ0_COLUMNS = {
    "kullanicilar": {
        "token_version": "INTEGER NOT NULL DEFAULT 0",
        "failed_login_count": "INTEGER NOT NULL DEFAULT 0",
        "locked_until": "TIMESTAMP NULL",
    }
}


def _read_password() -> str:
    env_password = os.environ.get("MALIASISTAN_ADMIN_PASSWORD")
    if env_password:
        return env_password
    first = getpass.getpass("Yönetici şifresi: ")
    second = getpass.getpass("Şifre (tekrar): ")
    if first != second:
        raise SystemExit("Şifreler eşleşmiyor.")
    return first


def _add_missing_columns(conn: Connection) -> list[str]:
    inspector = inspect(conn)
    added = []
    for table, columns in _FAZ0_COLUMNS.items():
        if not inspector.has_table(table):
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
                added.append(f"{table}.{name}")
    return added


async def secure_db() -> None:
    async with engine.begin() as conn:
        added = await conn.run_sync(_add_missing_columns)
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        disabled = await disable_legacy_default_accounts(session)
        remapped = 0
        for user in (await session.scalars(select(User))).all():
            role = normalize_role(user.rol)
            if role and role != user.rol:
                user.rol = role
                remapped += 1
        await session.commit()
    print(f"Eklenen sütunlar: {', '.join(added) or '-'}")
    print(f"Pasife alınan varsayılan hesaplar: {', '.join(disabled) or '-'}")
    print(f"Yeni rol modeline eşlenen kullanıcı sayısı: {remapped}")


async def _create_admin(args: argparse.Namespace) -> None:
    password = _read_password()
    try:
        validate_password_strength(password)
    except PasswordPolicyError as exc:
        raise SystemExit(str(exc)) from exc
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    admin = sub.add_parser("create-admin", help="İlk büro yöneticisini oluşturur.")
    admin.add_argument("--email", required=True)
    admin.add_argument("--ad-soyad", required=True)
    target = admin.add_mutually_exclusive_group(required=True)
    target.add_argument("--buro-id", help="Mevcut büronun UUID'si")
    target.add_argument("--buro-unvan", help="Yeni oluşturulacak büronun unvanı")

    sub.add_parser(
        "secure-db",
        help="Mevcut veritabanını Faz 0'a getirir: yeni sütunlar, varsayılan hesapların "
        "kapatılması, rol eşleme.",
    )

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        asyncio.run(_create_admin(args))
    elif args.command == "secure-db":
        asyncio.run(secure_db())


if __name__ == "__main__":
    sys.exit(main())
