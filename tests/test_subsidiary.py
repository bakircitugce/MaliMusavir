from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base
from app.ledger import JournalEntryDraft, JournalLine, account_movements, persist_draft


@pytest.mark.asyncio
async def test_account_movements_are_ordered_and_cumulative() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        for day, amount in ((3, Decimal("100.00")), (4, Decimal("50.00"))):
            await persist_draft(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                draft=JournalEntryDraft(
                    entry_date=date(2026, 11, day),
                    description=f"Kasa hareketi {day}",
                    lines=(
                        JournalLine("100.01.001", "Kasa", debit=amount),
                        JournalLine("600.03.001", "Satış", credit=amount),
                    ),
                ),
            )
        movements = await account_movements(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account_code="100.01.001",
        )

        assert [movement.debit for movement in movements] == [
            Decimal("100.00"),
            Decimal("50.00"),
        ]
        assert [movement.balance for movement in movements] == [
            Decimal("100.00"),
            Decimal("150.00"),
        ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_account_movements_apply_tenant_date_and_status_filters() -> None:
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
                description="Kapsam dışı kasa",
                lines=(
                    JournalLine("100.01.001", "Kasa", debit=Decimal("10.00")),
                    JournalLine("600.03.001", "Satış", credit=Decimal("10.00")),
                ),
            ),
        )
        movements = await account_movements(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            account_code="100.01.001",
            start_date=date(2026, 11, 1),
            status="approved",
        )
        assert movements == []
    await engine.dispose()
