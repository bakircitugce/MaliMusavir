"""Yönetim komutları.

    python -m app.cli migrate [--yedek-alindi]
    python -m app.cli create-admin --email ad@buro.com --ad-soyad "Ad Soyad" --buro-unvan "X SMMM"
    python -m app.cli create-admin --email ad@buro.com --ad-soyad "Ad Soyad" --buro-id <UUID>
    python -m app.cli secure-db
    python -m app.cli backfill-fis-no [--mukellef UUID]                  (dry-run, varsayılan)
    python -m app.cli backfill-fis-no --uygula --onay <parmak-izi>        (gerçek uygulama)

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

from app import audit

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
from app.core.schema_check import (
    SchemaNotReadyError,
    backup_sqlite,
    ensure_schema_ready,
    run_migrate,
    sqlite_path,
)
from app.ledger.backfill import BackfillError, apply_plan, build_plan


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


async def backfill_fis_no(args: argparse.Namespace) -> int:
    """Eski onaylı fişlere fiş numarası. Varsayılan dry-run; hiçbir şey yazmaz."""
    await _require_schema()
    mukellef_id = UUID(args.mukellef) if args.mukellef else None
    database_url = get_settings().database_url

    if not args.uygula:
        async with session_factory() as session:
            plan = await build_plan(session, mukellef_id=mukellef_id)
            await session.rollback()  # salt-okunur
        print("=== DRY-RUN: hiçbir değişiklik yapılmadı ===")
        for line in plan.report_lines():
            print(line)
        path = sqlite_path(database_url)
        if path is not None:
            print(f"Yedek: uygulama öncesi {path} için otomatik tutarlı yedek alınacak.")
        else:
            print("Yedek: bu veritabanında yedeği elle alıp --yedek-alindi vermek gerekecek.")
        if plan.blocked:
            return 2
        if plan.assignments:
            print(f"Uygulamak için (ONAYDAN SONRA): python -m app.cli backfill-fis-no --uygula "
                  f"--onay {plan.fingerprint}")
        return 0

    if not args.onay:
        raise SystemExit("--uygula için dry-run'daki plan parmak izi --onay ile verilmelidir.")
    if sqlite_path(database_url) is not None:
        backup = backup_sqlite(database_url, label="pre-backfill")
        if backup is None:
            raise SystemExit("Yedek alınamadı; işlem yapılmadı.")
        print(f"Yedek alındı: {backup}")
    elif not args.yedek_alindi:
        raise SystemExit("Bu veritabanında yedeği elle alıp --yedek-alindi ile çalıştırın.")
    async with session_factory() as session:
        try:
            plan = await apply_plan(session, expected_fingerprint=args.onay, mukellef_id=mukellef_id)
            await audit.record(
                session, "ledger.backfill_fis_no", target_type="backfill",
                details={"fingerprint": plan.fingerprint, "assigned": len(plan.assignments)},
                commit=False,
            )
            await session.commit()
        except BackfillError as exc:
            await session.rollback()
            print(f"UYGULANMADI: {exc}")
            return 1
    print(f"Uygulandı: {len(plan.assignments)} fişe numara verildi (plan {plan.fingerprint}).")
    return 0


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

    backfill = sub.add_parser(
        "backfill-fis-no",
        help="Eski onaylı fişlere fiş numarası (varsayılan DRY-RUN; --uygula için --onay gerekir).",
    )
    backfill.add_argument("--mukellef", help="Yalnızca bu mükellef (UUID)")
    backfill.add_argument("--uygula", action="store_true", help="Gerçek uygulama (dikkat)")
    backfill.add_argument("--onay", help="Dry-run'daki plan parmak izi")
    backfill.add_argument("--yedek-alindi", action="store_true",
                          help="SQLite dışında yedeğin elle alındığını beyan eder.")

    args = parser.parse_args(argv)
    if args.command == "migrate":
        return run_migrate(get_settings().database_url, assume_backup=args.yedek_alindi)
    if args.command == "backfill-fis-no":
        return asyncio.run(backfill_fis_no(args))
    if args.command == "create-admin":
        asyncio.run(_create_admin(args))
    elif args.command == "secure-db":
        asyncio.run(secure_db())
    return 0


if __name__ == "__main__":
    sys.exit(main())
