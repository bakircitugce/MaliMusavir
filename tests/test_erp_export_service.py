from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base, JournalEntry, JournalLine
from app.erp import InMemoryERPExportAdapter, export_approved_entry
from app.ledger import LedgerValidationError


class FailingERPAdapter:
    async def export_entry(self, document: object) -> object:
        raise RuntimeError("ERP bağlantısı başarısız")


@pytest.mark.asyncio
async def test_export_approved_entry_serializes_tenant_scoped_lines() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        entry = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 11, 4),
            description="ERP aktarım testi",
            status="approved",
        )
        session.add(entry)
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=entry.id,
                    line_order=1,
                    account_code="100.01.001",
                    account_name="Kasa",
                    debit=Decimal("100.00"),
                    credit=Decimal("0.00"),
                ),
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=entry.id,
                    line_order=2,
                    account_code="600.01.001",
                    account_name="Satış",
                    debit=Decimal("0.00"),
                    credit=Decimal("100.00"),
                ),
            ]
        )
        await session.flush()

        result = await export_approved_entry(
            session,
            InMemoryERPExportAdapter(),
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            journal_entry_id=entry.id,
        )

        assert result.was_duplicate is False
        assert result.idempotency_key.endswith(str(entry.id))
    await engine.dispose()


@pytest.mark.asyncio
async def test_export_approved_entry_rejects_draft() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        entry = JournalEntry(
            buro_id=uuid4(),
            mukellef_id=uuid4(),
            entry_date=date(2026, 11, 4),
            description="Taslak",
            status="draft",
        )
        session.add(entry)
        await session.flush()

        with pytest.raises(LedgerValidationError, match="onaylı"):
            await export_approved_entry(
                session,
                InMemoryERPExportAdapter(),
                buro_id=entry.buro_id,
                mukellef_id=entry.mukellef_id,
                journal_entry_id=entry.id,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_export_approved_entry_records_failure() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        entry = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 11, 4),
            description="ERP hata testi",
            status="approved",
        )
        session.add(entry)
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=entry.id,
                    line_order=1,
                    account_code="100.01.001",
                    account_name="Kasa",
                    debit=Decimal("10.00"),
                    credit=Decimal("0.00"),
                ),
                JournalLine(
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    journal_entry_id=entry.id,
                    line_order=2,
                    account_code="600.01.001",
                    account_name="Satış",
                    debit=Decimal("0.00"),
                    credit=Decimal("10.00"),
                ),
            ]
        )
        await session.flush()

        with pytest.raises(RuntimeError, match="bağlantısı"):
            await export_approved_entry(
                session,
                FailingERPAdapter(),
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                journal_entry_id=entry.id,
            )

        from app.erp import list_export_records

        records = await list_export_records(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            status="failed",
        )
        assert len(records) == 1
        assert records[0].error_message == "ERP bağlantısı başarısız"
    await engine.dispose()
