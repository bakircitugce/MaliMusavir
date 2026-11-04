from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base
from app.ledger import JournalEntryDraft, JournalLine, persist_draft, trial_balance


@pytest.mark.asyncio
async def test_trial_balance_groups_journal_lines_by_account() -> None:
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
                description="Mizan fişi 1",
                lines=(
                    JournalLine("100.01.001", "Kasa", debit=Decimal("100.00")),
                    JournalLine("600.03.001", "Satış", credit=Decimal("100.00")),
                ),
            ),
        )
        await persist_draft(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            draft=JournalEntryDraft(
                entry_date=date(2026, 11, 4),
                description="Mizan fişi 2",
                lines=(
                    JournalLine("100.01.001", "Kasa", debit=Decimal("50.00")),
                    JournalLine("600.03.001", "Satış", credit=Decimal("50.00")),
                ),
            ),
        )

        result = await trial_balance(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        )

        assert [(item.account_code, item.total_debit, item.total_credit) for item in result] == [
            ("100.01.001", Decimal("150.00"), Decimal("0.00")),
            ("600.03.001", Decimal("0.00"), Decimal("150.00")),
        ]
        assert result[0].balance == Decimal("150.00")
    await engine.dispose()


@pytest.mark.asyncio
async def test_trial_balance_excludes_other_tenant_and_status() -> None:
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
                description="Taslak fiş",
                lines=(
                    JournalLine("100.01.001", "Kasa", debit=Decimal("10.00")),
                    JournalLine("600.03.001", "Satış", credit=Decimal("10.00")),
                ),
            ),
        )
        result = await trial_balance(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            status="approved",
        )
        assert result == []
    await engine.dispose()
