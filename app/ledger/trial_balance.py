from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import JournalEntry, JournalLine
from app.ledger.draft import LedgerValidationError


@dataclass(frozen=True)
class AccountBalance:
    account_code: str
    account_name: str
    total_debit: Decimal
    total_credit: Decimal

    @property
    def balance(self) -> Decimal:
        return self.total_debit - self.total_credit


async def trial_balance(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    start_date: date | None = None,
    end_date: date | None = None,
    status: str | None = None,
) -> list[AccountBalance]:
    statement = (
        select(
            JournalLine.account_code,
            JournalLine.account_name,
            func.coalesce(func.sum(JournalLine.debit), 0),
            func.coalesce(func.sum(JournalLine.credit), 0),
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
        .where(
            JournalLine.buro_id == buro_id,
            JournalLine.mukellef_id == mukellef_id,
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
        )
        .group_by(JournalLine.account_code, JournalLine.account_name)
        .order_by(JournalLine.account_code)
    )
    if start_date is not None:
        statement = statement.where(JournalEntry.entry_date >= start_date)
    if end_date is not None:
        statement = statement.where(JournalEntry.entry_date <= end_date)
    if status is not None:
        statement = statement.where(JournalEntry.status == status)

    rows = (await session.execute(statement)).all()
    result = [
        AccountBalance(
            account_code=account_code,
            account_name=account_name,
            total_debit=Decimal(str(total_debit)),
            total_credit=Decimal(str(total_credit)),
        )
        for account_code, account_name, total_debit, total_credit in rows
    ]
    if sum((item.total_debit for item in result), Decimal("0")) != sum(
        (item.total_credit for item in result), Decimal("0")
    ):
        raise LedgerValidationError("Mizan özeti dengede değil.")
    return result
