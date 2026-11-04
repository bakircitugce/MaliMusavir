from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import AccountingPeriod
from app.ledger.draft import LedgerValidationError


async def open_period(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    start_date: date,
    end_date: date,
) -> AccountingPeriod:
    if start_date > end_date:
        raise LedgerValidationError("Dönem başlangıç tarihi bitiş tarihinden sonra olamaz.")
    overlapping = await session.execute(
        select(AccountingPeriod.id).where(
            AccountingPeriod.buro_id == buro_id,
            AccountingPeriod.mukellef_id == mukellef_id,
            AccountingPeriod.start_date <= end_date,
            AccountingPeriod.end_date >= start_date,
        )
    )
    if overlapping.scalar_one_or_none() is not None:
        raise LedgerValidationError("Aynı mükellef için çakışan muhasebe dönemi var.")
    period = AccountingPeriod(
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        start_date=start_date,
        end_date=end_date,
        status="open",
    )
    session.add(period)
    await session.flush()
    return period


async def lock_period(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    period_id: UUID,
) -> AccountingPeriod:
    result = await session.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.id == period_id,
            AccountingPeriod.buro_id == buro_id,
            AccountingPeriod.mukellef_id == mukellef_id,
        )
    )
    period = result.scalar_one_or_none()
    if period is None:
        raise LedgerValidationError("Muhasebe dönemi bulunamadı.")
    if period.status == "locked":
        raise LedgerValidationError("Muhasebe dönemi zaten kilitli.")
    period.status = "locked"
    await session.flush()
    return period
