from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bank import (
    BankAccountInput,
    BankTransactionInput,
    create_bank_account,
    import_bank_transactions,
)
from app.core.models import Base
from app.ledger import LedgerValidationError


def make_transaction(external_id: str, iban: str = "TR00") -> BankTransactionInput:
    return BankTransactionInput(
        bank_account_iban=iban,
        external_id=external_id,
        transaction_date=date(2026, 11, 3),
        description="Örnek banka hareketi",
        debit=Decimal("100.00"),
        credit=Decimal("0.00"),
    )


@pytest.mark.asyncio
async def test_import_bank_transactions_is_idempotent() -> None:
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
        transactions = [make_transaction("IMPORT-001"), make_transaction("IMPORT-002")]
        first = await import_bank_transactions(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transactions=transactions,
        )
        second = await import_bank_transactions(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transactions=transactions,
        )

        assert first.created_count == 2
        assert first.skipped_count == 0
        assert second.created_count == 0
        assert second.skipped_count == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_import_bank_transactions_rejects_duplicate_input_ids() -> None:
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
        with pytest.raises(LedgerValidationError, match="aynı IBAN"):
            await import_bank_transactions(
                session,
                buro_id=uuid4(),
                mukellef_id=uuid4(),
                transactions=[make_transaction("IMPORT-003"), make_transaction("IMPORT-003")],
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_import_bank_transactions_keeps_same_external_id_on_different_ibans() -> None:
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
            account=BankAccountInput("TR01", "Demo Banka", "Vadesiz 1", "102.01.001"),
        )
        await create_bank_account(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account=BankAccountInput("TR02", "Demo Banka", "Vadesiz 2", "102.01.002"),
        )
        result = await import_bank_transactions(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transactions=[
                make_transaction("IMPORT-004", "TR01"),
                make_transaction("IMPORT-004", "TR02"),
            ],
        )

        assert result.created_count == 2
        assert result.skipped_count == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_import_bank_transactions_allows_same_key_in_different_buros() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    mukellef_id = uuid4()
    first_buro_id = uuid4()
    second_buro_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        for buro_id in (first_buro_id, second_buro_id):
            await create_bank_account(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                account=BankAccountInput("TR03", "Demo Banka", str(buro_id), "102.01.003"),
            )

        first = await import_bank_transactions(
            session,
            buro_id=first_buro_id,
            mukellef_id=mukellef_id,
            transactions=[make_transaction("SHARED-ID", "TR03")],
        )
        second = await import_bank_transactions(
            session,
            buro_id=second_buro_id,
            mukellef_id=mukellef_id,
            transactions=[make_transaction("SHARED-ID", "TR03")],
        )

        assert first.created_count == 1
        assert second.created_count == 1
    await engine.dispose()
