from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bank import BankAccountInput, create_bank_account, import_bank_csv
from app.core.models import Base
from app.ledger import LedgerValidationError


CSV_TEXT = (
    "external_id;transaction_date;description;debit;credit;balance\n"
    "SERVICE-001;2026-11-03;Tahsilat;1.200,00;;5.000,00\n"
)


@pytest.mark.asyncio
async def test_import_bank_csv_parses_and_imports_idempotently() -> None:
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
            account=BankAccountInput("TR00", "Demo", "Vadesiz", "102.01.001"),
        )
        first = await import_bank_csv(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            csv_text=CSV_TEXT,
            bank_account_iban="TR00",
        )
        second = await import_bank_csv(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            csv_text=CSV_TEXT,
            bank_account_iban="TR00",
        )
        assert first.created_count == 1
        assert second.skipped_count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_import_bank_csv_rejects_unknown_account() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        with pytest.raises(LedgerValidationError, match="Aktif banka"):
            await import_bank_csv(
                session,
                buro_id=uuid4(),
                mukellef_id=uuid4(),
                csv_text=CSV_TEXT,
                bank_account_iban="TR00",
            )
    await engine.dispose()
