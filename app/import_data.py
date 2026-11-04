import argparse
import asyncio
from pathlib import Path
from uuid import UUID

from app.chart.import_mizan import import_mizan_workbook
from app.chart.import_service import import_mizan_rows, import_nace_records
from app.core.db import session_factory
from app.nace.importer import import_nace_workbook


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MaliAsistan Excel veri içe aktarıcı")
    parser.add_argument("--nace", type=Path, help="NACE Excel dosyası")
    parser.add_argument("--mizan", type=Path, help="Mizan Excel dosyası")
    parser.add_argument("--buro-id", type=UUID, help="Mizan için mevcut büro UUID'si")
    parser.add_argument("--mukellef-id", type=UUID, help="Mizan için mevcut mükellef UUID'si")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Excel'leri doğrula ve sayımları yazdır; veritabanına yazma",
    )
    return parser


async def run_import(args: argparse.Namespace) -> None:
    nace_records = []
    mizan_rows = []
    if args.nace:
        related, current = import_nace_workbook(args.nace)
        nace_records = related + current
        print(f"NACE: iliskili={len(related)}, guncel={len(current)}")
    if args.mizan:
        mizan_rows, difference = import_mizan_workbook(args.mizan)
        print(f"Mizan: hesap={len(mizan_rows)}, denge_farki={difference}")
        if not args.dry_run and (args.buro_id is None or args.mukellef_id is None):
            raise SystemExit("--mizan ile birlikte --buro-id ve --mukellef-id zorunludur")

    if args.dry_run:
        print("Dry-run tamamlandi; veritabanina yazilmadi.")
        return

    async with session_factory() as session:
        if nace_records:
            summary = await import_nace_records(session, nace_records)
            print(f"NACE import: {summary}")
        if mizan_rows:
            summary = await import_mizan_rows(
                session,
                args.buro_id,
                args.mukellef_id,
                mizan_rows,
            )
            print(f"Mizan import: {summary}")


def main() -> None:
    args = build_parser().parse_args()
    if not args.nace and not args.mizan:
        raise SystemExit("En az bir --nace veya --mizan dosyası verilmelidir")
    asyncio.run(run_import(args))


if __name__ == "__main__":
    main()
