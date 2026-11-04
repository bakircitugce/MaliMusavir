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
    ignore_bank_transaction,
    list_bank_transactions,
)
from app.core.models import Base
from app.ledger import LedgerValidationError


@pytest.mark.asyncio
async def test_create_bank_transaction_stores_unmatched_movement() -> None:
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
                external_id="BANK-001",
                transaction_date=date(2026, 11, 3),
                description="Örnek banka tahsilatı",
                debit=Decimal("1000.00"),
                credit=Decimal("0.00"),
                balance=Decimal("5000.00"),
            ),
        )

        assert movement.reconciliation_status == "unmatched"
        assert movement.debit == Decimal("1000.00")
    await engine.dispose()


@pytest.mark.asyncio
async def test_bank_iban_is_normalized_before_storage() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        buro_id = uuid4()
        mukellef_id = uuid4()
        account = await create_bank_account(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account=BankAccountInput(" tr 00 ", "Demo Banka", "Vadesiz", "102.01.001"),
        )
        movement = await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban=" Tr 00 ",
                external_id="BANK-NORMALIZED",
                transaction_date=date(2026, 11, 3),
                description="Normalize edilmiş hareket",
                debit=Decimal("10.00"),
                credit=Decimal("0.00"),
            ),
        )

        assert account.iban == "TR00"
        assert movement.bank_account_iban == "TR00"
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_bank_transaction_requires_one_direction() -> None:
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
        with pytest.raises(LedgerValidationError, match="yalnızca"):
            await create_bank_transaction(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                transaction=BankTransactionInput(
                    bank_account_iban="TR00",
                    external_id="BANK-002",
                    transaction_date=date(2026, 11, 3),
                    description="Geçersiz hareket",
                    debit=Decimal("10.00"),
                    credit=Decimal("10.00"),
                ),
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_bank_transactions_filters_tenant_date_iban_and_status() -> None:
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
                external_id="BANK-003",
                transaction_date=date(2026, 11, 3),
                description="Filtrelenecek hareket",
                debit=Decimal("10.00"),
                credit=Decimal("0.00"),
            ),
        )
        other_buro_id = uuid4()
        await create_bank_account(
            session,
            buro_id=other_buro_id,
            mukellef_id=mukellef_id,
            account=BankAccountInput("TR00", "Demo Banka", "Diğer", "102.01.002"),
        )
        await create_bank_transaction(
            session,
            buro_id=other_buro_id,
            mukellef_id=mukellef_id,
            transaction=BankTransactionInput(
                bank_account_iban="TR00",
                external_id="BANK-004",
                transaction_date=date(2026, 11, 3),
                description="Başka tenant hareketi",
                debit=Decimal("20.00"),
                credit=Decimal("0.00"),
            ),
        )
        movements = await list_bank_transactions(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            bank_account_iban="TR00",
            start_date=date(2026, 11, 1),
            reconciliation_status="unmatched",
        )
        assert [movement.external_id for movement in movements] == ["BANK-003"]

        with pytest.raises(LedgerValidationError, match="Geçersiz"):
            await list_bank_transactions(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                reconciliation_status="invalid",
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_ignore_bank_transaction_removes_movement_from_unmatched_queue() -> None:
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
                external_id="IGNORE-001",
                transaction_date=date(2026, 11, 3),
                description="Banka ücret kesintisi",
                debit=Decimal("10.00"),
                credit=Decimal("0.00"),
            ),
        )

        ignored = await ignore_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction_id=movement.id,
        )

        assert ignored.reconciliation_status == "ignored"
        with pytest.raises(LedgerValidationError, match="eşleşmemiş"):
            await ignore_bank_transaction(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                transaction_id=movement.id,
            )
    await engine.dispose()
