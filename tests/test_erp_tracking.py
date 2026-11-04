from datetime import date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base
from app.erp import ERPExportResult, record_export_result


@pytest.mark.asyncio
async def test_record_export_result_is_idempotent() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        buro_id = uuid4()
        mukellef_id = uuid4()
        entry_id = uuid4()
        key = f"{buro_id}:{mukellef_id}:{entry_id}"
        result = ERPExportResult(key, "ERP-001", datetime.now(), False)

        # The tracking row references the journal entry; create it through the model
        # in a focused fixture to keep this test independent of ledger services.
        from app.core.models import JournalEntry

        session.add(
            JournalEntry(
                id=entry_id,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                entry_date=date(2026, 11, 4),
                description="ERP takip",
                status="approved",
            )
        )
        await session.flush()
        first = await record_export_result(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            journal_entry_id=entry_id,
            idempotency_key=key,
            result=result,
        )
        second = await record_export_result(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            journal_entry_id=entry_id,
            idempotency_key=key,
            result=result,
        )

        assert first.id == second.id
        assert second.status == "exported"
        assert second.external_reference == "ERP-001"
    await engine.dispose()
