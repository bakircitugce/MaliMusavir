from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chart.import_mizan import MizanRow
from app.core.models import Hesap, NaceCode
from app.nace.importer import NaceRecord


@dataclass(frozen=True)
class ImportSummary:
    inserted: int
    updated: int
    unchanged: int


async def import_nace_records(
    session: AsyncSession,
    records: list[NaceRecord],
) -> ImportSummary:
    inserted = updated = unchanged = 0
    for record in records:
        existing = await session.scalar(
            select(NaceCode).where(
                NaceCode.kod == record.kod,
                NaceCode.kaynak == record.kaynak,
            )
        )
        if existing is None:
            session.add(
                NaceCode(
                    kod=record.kod,
                    tanim=record.tanim,
                    kaynak=record.kaynak,
                )
            )
            inserted += 1
        elif (
            existing.tanim != record.tanim
        ):
            existing.tanim = record.tanim
            updated += 1
        else:
            unchanged += 1
    await session.commit()
    return ImportSummary(inserted, updated, unchanged)


async def import_mizan_rows(
    session: AsyncSession,
    buro_id: UUID,
    mukellef_id: UUID,
    rows: list[MizanRow],
) -> ImportSummary:
    inserted = updated = unchanged = 0
    for row in rows:
        existing = await session.scalar(
            select(Hesap).where(
                Hesap.mukellef_id == mukellef_id,
                Hesap.kod == row.hesap_kodu,
            )
        )
        values = {
            "buro_id": buro_id,
            "mukellef_id": mukellef_id,
            "kod": row.hesap_kodu,
            "ad": row.hesap_adi,
            "seviye": len(row.hesap_kodu.split(".")),
            "ust_kod": ".".join(row.hesap_kodu.split(".")[:-1]) or None,
            "acilis_borcu": row.borc,
            "acilis_alacagi": row.alacak,
        }
        if existing is None:
            session.add(Hesap(**values))
            inserted += 1
        elif any(getattr(existing, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(existing, key, value)
            updated += 1
        else:
            unchanged += 1
    await session.commit()
    return ImportSummary(inserted, updated, unchanged)
