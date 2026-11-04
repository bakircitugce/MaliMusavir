from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import JournalEntry, JournalLine
from app.ledger.draft import LedgerValidationError


@dataclass(frozen=True)
class JournalSummary:
    entry_count: int
    total_debit: Decimal
    total_credit: Decimal

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit


async def summarize_journal(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    start_date: date | None = None,
    end_date: date | None = None,
    status: str | None = None,
) -> JournalSummary:
    statement = select(JournalEntry.id).where(
        JournalEntry.buro_id == buro_id,
        JournalEntry.mukellef_id == mukellef_id,
    )
    if start_date is not None:
        statement = statement.where(JournalEntry.entry_date >= start_date)
    if end_date is not None:
        statement = statement.where(JournalEntry.entry_date <= end_date)
    if status is not None:
        statement = statement.where(JournalEntry.status == status)
    entry_ids = statement.subquery()

    totals = await session.execute(
        select(
            func.count(func.distinct(JournalLine.journal_entry_id)),
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        ).where(
            JournalLine.buro_id == buro_id,
            JournalLine.mukellef_id == mukellef_id,
            JournalLine.journal_entry_id.in_(select(entry_ids.c.id)),
        )
    )
    entry_count, total_debit, total_credit = totals.one()
    summary = JournalSummary(
        entry_count=entry_count,
        total_debit=Decimal(str(total_debit)),
        total_credit=Decimal(str(total_credit)),
    )
    if not summary.is_balanced:
        raise LedgerValidationError(
            f"Yevmiye özeti dengede değil: borç={summary.total_debit}, "
            f"alacak={summary.total_credit}."
        )
    return summary
