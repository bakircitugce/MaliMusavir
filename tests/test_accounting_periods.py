from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base
from app.ledger import LedgerValidationError, lock_period, open_period


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_open_and_lock_period(session) -> None:
    buro_id = uuid4()
    mukellef_id = uuid4()
    period = await open_period(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 30),
    )
    assert period.status == "open"

    locked = await lock_period(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        period_id=period.id,
    )
    assert locked.status == "locked"


@pytest.mark.asyncio
async def test_open_period_rejects_overlap(session) -> None:
    buro_id = uuid4()
    mukellef_id = uuid4()
    await open_period(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 30),
    )

    with pytest.raises(LedgerValidationError, match="çakışan"):
        await open_period(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            start_date=date(2026, 11, 15),
            end_date=date(2026, 12, 15),
        )


@pytest.mark.asyncio
async def test_lock_period_enforces_tenant_scope(session) -> None:
    period = await open_period(
        session,
        buro_id=uuid4(),
        mukellef_id=uuid4(),
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 30),
    )

    with pytest.raises(LedgerValidationError, match="bulunamadı"):
        await lock_period(
            session,
            buro_id=uuid4(),
            mukellef_id=uuid4(),
            period_id=period.id,
        )
