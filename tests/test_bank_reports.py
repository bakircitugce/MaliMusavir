from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bank import (
    BankAccountInput,
    BankTransactionInput,
    create_bank_account,
    create_bank_transaction,
    list_reconciliation_queue,
    summarize_reconciliation,
)
from app.core.models import Base
from app.core.models import JournalEntry, JournalLine


@pytest.mark.asyncio
async def test_reconciliation_summary_groups_statuses_and_amounts() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await create_bank_account(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account=BankAccountInput("TR00", "Demo Banka", "Vadesiz", "102.01.001"),
        )
        await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="REPORT-001",
                transaction_date=date(2026, 11, 3),
                description="Eşleşmeyen hareket",
                debit=Decimal("100.00"),
                credit=Decimal("0.00"),
            ),
        )
        matched = await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="REPORT-002",
                transaction_date=date(2026, 11, 3),
                description="Eşleşen hareket",
                debit=Decimal("0.00"),
                credit=Decimal("50.00"),
            ),
        )
        matched.reconciliation_status = "matched"

        summary = await summarize_reconciliation(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            bank_account_iban="TR00",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        )

        assert summary.transaction_count == 2
        assert summary.unmatched_count == 1
        assert summary.matched_count == 1
        assert summary.unmatched_amount == Decimal("100.00")
        assert summary.matched_amount == Decimal("50.00")

        queue = await list_reconciliation_queue(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            bank_account_iban=" tr 00 ",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        )
        assert [item.external_id for item in queue] == ["REPORT-001"]

        with pytest.raises(ValueError, match="1 ile 1000"):
            await list_reconciliation_queue(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                limit=0,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_pos_aging_groups_unmatched_108_credits() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        entries = [
            JournalEntry(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                entry_date=date(2026, 11, 3),
                description="Güncel POS",
                status="approved",
            ),
            JournalEntry(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                entry_date=date(2026, 10, 1),
                description="Eski POS",
                status="approved",
            ),
        ]
        session.add_all(entries)
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=entries[0].id,
                    line_order=1,
                    account_code="108.01.001",
                    account_name="POS",
                    debit=Decimal("0.00"),
                    credit=Decimal("100.00"),
                ),
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=entries[1].id,
                    line_order=1,
                    account_code="108.01.001",
                    account_name="POS",
                    debit=Decimal("0.00"),
                    credit=Decimal("250.00"),
                ),
            ]
        )
        await session.flush()

        from app.bank import summarize_pos_aging

        summary = await summarize_pos_aging(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            as_of_date=date(2026, 11, 3),
        )

        assert summary.total_count == 2
        assert summary.total_amount == Decimal("350.00")
        assert summary.buckets[0].amount == Decimal("100.00")
        assert summary.buckets[1].amount == Decimal("250.00")
    await engine.dispose()
