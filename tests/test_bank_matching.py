from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bank import (
    BankAccountInput,
    BankTransactionInput,
    create_bank_account,
    create_bank_transaction,
    list_reconciliation_candidates,
    list_reconciliation_group_candidates,
)
from app.core.models import Base, BankTransaction, JournalEntry, JournalLine
from app.ledger import LedgerValidationError


@pytest.mark.asyncio
async def test_reconciliation_candidates_match_amount_and_rank_by_date() -> None:
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
        movement = await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="MATCH-001",
                transaction_date=date(2026, 11, 3),
                description="Banka tahsilatı",
                debit=Decimal("100.00"),
                credit=Decimal("0.00"),
            ),
        )
        exact = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 11, 3),
            description="Tam tarihli tahsilat",
            status="approved",
        )
        nearby = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 11, 5),
            description="Yakın tarihli tahsilat",
            status="approved",
        )
        draft = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 11, 3),
            description="Taslak fiş",
            status="draft",
        )
        session.add_all((exact, nearby, draft))
        await session.flush()
        for entry in (exact, nearby, draft):
            session.add(
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=entry.id,
                    line_order=1,
                    account_code="102.01.001",
                    account_name="Banka",
                    debit=Decimal("100.00"),
                    credit=Decimal("0.00"),
                )
            )
        await session.flush()
        already_matched = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 11, 3),
            description="Başka hareketle eşleşmiş",
            status="approved",
        )
        session.add(already_matched)
        await session.flush()
        session.add(
            JournalLine(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                journal_entry_id=already_matched.id,
                line_order=1,
                account_code="102.01.001",
                account_name="Banka",
                debit=Decimal("100.00"),
                credit=Decimal("0.00"),
            )
        )
        await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="MATCH-003",
                transaction_date=date(2026, 11, 3),
                description="Önceden eşleşmiş hareket",
                debit=Decimal("100.00"),
                credit=Decimal("0.00"),
            ),
        )
        existing_match = (
            await session.execute(
                select(BankTransaction).where(
                    BankTransaction.external_id == "MATCH-003"
                )
            )
        ).scalar_one()
        existing_match.reconciliation_status = "matched"
        existing_match.matched_journal_entry_id = already_matched.id
        await session.flush()

        candidates = await list_reconciliation_candidates(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction_id=movement.id,
        )

        assert [candidate.description for candidate in candidates] == [
            "Tam tarihli tahsilat",
            "Yakın tarihli tahsilat",
        ]
        assert candidates[0].score == 100
        assert candidates[1].date_distance == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_candidates_reject_matched_transaction() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        buro_id = uuid4()
        mukellef_id = uuid4()
        await create_bank_account(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account=BankAccountInput("TR00", "Demo Banka", "Vadesiz", "102.01.001"),
        )
        movement = await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="MATCH-002",
                transaction_date=date(2026, 11, 3),
                description="Mutabık hareket",
                debit=Decimal("10.00"),
                credit=Decimal("0.00"),
            ),
        )
        movement.reconciliation_status = "matched"

        with pytest.raises(LedgerValidationError, match="eşleşmemiş"):
            await list_reconciliation_candidates(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                transaction_id=movement.id,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_candidates_match_outgoing_bank_transaction() -> None:
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
        movement = await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="OUTGOING-001",
                transaction_date=date(2026, 11, 3),
                description="Tedarikçi ödemesi",
                debit=Decimal("0.00"),
                credit=Decimal("75.00"),
            ),
        )
        entry = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 11, 3),
            description="Tedarikçi ödeme fişi",
            status="approved",
        )
        session.add(entry)
        await session.flush()
        session.add(
            JournalLine(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                journal_entry_id=entry.id,
                line_order=1,
                account_code="320.01.001",
                account_name="Satıcı",
                debit=Decimal("75.00"),
                credit=Decimal("0.00"),
            )
        )
        session.add(
            JournalLine(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                journal_entry_id=entry.id,
                line_order=2,
                account_code="102.01.001",
                account_name="Banka",
                debit=Decimal("0.00"),
                credit=Decimal("75.00"),
            )
        )
        await session.flush()

        candidates = await list_reconciliation_candidates(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction_id=movement.id,
        )

        assert len(candidates) == 1
        assert candidates[0].amount == Decimal("75.00")
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_group_candidates_match_multiple_entries() -> None:
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
        movement = await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="GROUP-001",
                transaction_date=date(2026, 11, 3),
                description="Toplu POS yatışı",
                debit=Decimal("300.00"),
                credit=Decimal("0.00"),
            ),
        )
        entries = [
            JournalEntry(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                entry_date=date(2026, 11, 2),
                description="Z raporu 1",
                status="approved",
            ),
            JournalEntry(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                entry_date=date(2026, 11, 3),
                description="Z raporu 2",
                status="approved",
            ),
        ]
        session.add_all(entries)
        await session.flush()
        for entry, amount in zip(entries, (Decimal("120.00"), Decimal("180.00"))):
            session.add(
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=entry.id,
                    line_order=1,
                    account_code="102.01.001",
                    account_name="Banka",
                    debit=amount,
                    credit=Decimal("0.00"),
                )
            )
        await session.flush()

        candidates = await list_reconciliation_group_candidates(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction_id=movement.id,
        )

        assert len(candidates) == 1
        assert set(candidates[0].journal_entry_ids) == {entries[0].id, entries[1].id}
        assert candidates[0].amount == Decimal("300.00")
    await engine.dispose()
