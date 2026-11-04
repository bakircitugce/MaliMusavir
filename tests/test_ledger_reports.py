from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base
from app.ledger import (
    JournalEntryDraft,
    JournalLine,
    persist_draft,
    summarize_journal,
)


@pytest.mark.asyncio
async def test_summarize_journal_returns_balanced_totals() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await persist_draft(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            draft=JournalEntryDraft(
                entry_date=date(2026, 11, 3),
                description="Rapor fişi",
                lines=(
                    JournalLine("100.01.001", "Kasa", debit=Decimal("10.00")),
                    JournalLine("600.03.001", "Satış", credit=Decimal("10.00")),
                ),
            ),
        )
        summary = await summarize_journal(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        )

        assert summary.entry_count == 1
        assert summary.total_debit == Decimal("10.00")
        assert summary.total_credit == Decimal("10.00")
        assert summary.is_balanced
    await engine.dispose()


@pytest.mark.asyncio
async def test_summarize_journal_excludes_other_tenants_and_dates() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await persist_draft(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            draft=JournalEntryDraft(
                entry_date=date(2026, 10, 31),
                description="Kapsam dışı fiş",
                lines=(
                    JournalLine("100.01.001", "Kasa", debit=Decimal("50.00")),
                    JournalLine("600.03.001", "Satış", credit=Decimal("50.00")),
                ),
            ),
        )
        summary = await summarize_journal(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            start_date=date(2026, 11, 1),
        )
        assert summary.entry_count == 0
        assert summary.total_debit == Decimal("0")
    await engine.dispose()
