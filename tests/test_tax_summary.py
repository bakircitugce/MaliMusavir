from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base, JournalEntry, JournalLine
from app.tax import summarize_approved_entries


@pytest.mark.asyncio
async def test_tax_summary_uses_approved_entries_and_returns_sources() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    buro_id = uuid4()
    mukellef_id = uuid4()
    other_mukellef_id = uuid4()
    async with factory() as session:
        approved = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 11, 4),
            description="Onaylı satış",
            status="approved",
        )
        draft = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 11, 4),
            description="Taslak satış",
            status="draft",
        )
        other = JournalEntry(
            buro_id=buro_id,
            mukellef_id=other_mukellef_id,
            entry_date=date(2026, 11, 4),
            description="Başka mükellef",
            status="approved",
        )
        session.add_all([approved, draft, other])
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=approved.id,
                    line_order=1,
                    account_code="600.01",
                    account_name="Satış",
                    debit=Decimal("0"),
                    credit=Decimal("100"),
                ),
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=draft.id,
                    line_order=1,
                    account_code="600.01",
                    account_name="Taslak",
                    debit=Decimal("0"),
                    credit=Decimal("999"),
                ),
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=other_mukellef_id,
                    journal_entry_id=other.id,
                    line_order=1,
                    account_code="600.01",
                    account_name="Başka mükellef",
                    debit=Decimal("0"),
                    credit=Decimal("500"),
                ),
            ]
        )
        await session.flush()

        lines = await summarize_approved_entries(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
            account_prefixes={"sales": ("600",)},
        )

        assert lines[0].credit == Decimal("100.00")
        assert lines[0].debit == Decimal("0.00")
        assert lines[0].net == Decimal("-100.00")
        assert lines[0].journal_entry_ids == (approved.id,)
    await engine.dispose()


@pytest.mark.asyncio
async def test_tax_summary_rejects_reversed_period() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        with pytest.raises(ValueError, match="Başlangıç"):
            await summarize_approved_entries(
                session,
                buro_id=uuid4(),
                mukellef_id=uuid4(),
                start_date=date(2026, 11, 30),
                end_date=date(2026, 11, 1),
                account_prefixes={"sales": ("600",)},
            )
    await engine.dispose()
