from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import JournalEntry, JournalLine
from app.ledger.draft import LedgerValidationError


@dataclass(frozen=True)
class SubsidiaryMovement:
    entry_id: UUID
    entry_date: date
    description: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


async def account_movements(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    account_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
    status: str | None = None,
) -> list[SubsidiaryMovement]:
    if not account_code.strip():
        raise LedgerValidationError("Hesap kodu boş olamaz.")
    statement = (
        select(JournalEntry, JournalLine)
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .where(
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
            JournalLine.buro_id == buro_id,
            JournalLine.mukellef_id == mukellef_id,
            JournalLine.account_code == account_code,
        )
        .order_by(JournalEntry.entry_date, JournalEntry.created_at, JournalLine.line_order)
    )
    if start_date is not None:
        statement = statement.where(JournalEntry.entry_date >= start_date)
    if end_date is not None:
        statement = statement.where(JournalEntry.entry_date <= end_date)
    if status is not None:
        statement = statement.where(JournalEntry.status == status)

    rows = (await session.execute(statement)).all()
    balance = Decimal("0.00")
    movements: list[SubsidiaryMovement] = []
    for entry, line in rows:
        balance += line.debit - line.credit
        movements.append(
            SubsidiaryMovement(
                entry_id=entry.id,
                entry_date=entry.entry_date,
                description=entry.description,
                debit=Decimal(str(line.debit)),
                credit=Decimal(str(line.credit)),
                balance=balance,
            )
        )
    return movements
