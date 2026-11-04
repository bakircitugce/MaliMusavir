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
    reconcile_bank_transaction,
)
from app.core.models import Base
from app.ledger import (
    JournalEntryDraft,
    JournalLine,
    LedgerValidationError,
    approve_entry,
    persist_draft,
)


async def make_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_reconcile_bank_transaction_matches_approved_entry() -> None:
    engine, factory = await make_database()
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with factory() as session:
        await create_bank_account(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account=BankAccountInput("TR00", "Demo Banka", "Vadesiz", "102.01.001"),
        )
        transaction = await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="BANK-RECON-001",
                transaction_date=date(2026, 11, 3),
                description="POS tahsilatı",
                debit=Decimal("120.00"),
                credit=Decimal("0.00"),
            ),
        )
        entry = await persist_draft(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            draft=JournalEntryDraft(
                entry_date=date(2026, 11, 3),
                description="POS fişi",
                lines=(
                    JournalLine("102.01.001", "Banka", debit=Decimal("120.00")),
                    JournalLine("108.01.001", "POS", credit=Decimal("120.00")),
                ),
            ),
        )
        await approve_entry(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_id=entry.id,
        )

        matched = await reconcile_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction_id=transaction.id,
            journal_entry_id=entry.id,
        )

        assert matched.reconciliation_status == "matched"
        assert matched.matched_journal_entry_id == entry.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_bank_transaction_rejects_amount_mismatch() -> None:
    engine, factory = await make_database()
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with factory() as session:
        await create_bank_account(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account=BankAccountInput("TR00", "Demo Banka", "Vadesiz", "102.01.001"),
        )
        transaction = await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="BANK-RECON-002",
                transaction_date=date(2026, 11, 3),
                description="Uyumsuz tahsilat",
                debit=Decimal("100.00"),
                credit=Decimal("0.00"),
            ),
        )
        entry = await persist_draft(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            draft=JournalEntryDraft(
                entry_date=date(2026, 11, 3),
                description="Farklı tutarlı fiş",
                lines=(
                    JournalLine("102.01.001", "Banka", debit=Decimal("120.00")),
                    JournalLine("108.01.001", "POS", credit=Decimal("120.00")),
                ),
            ),
        )
        await approve_entry(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_id=entry.id,
        )

        with pytest.raises(LedgerValidationError, match="eşleşmiyor"):
            await reconcile_bank_transaction(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                transaction_id=transaction.id,
                journal_entry_id=entry.id,
            )
    await engine.dispose()
