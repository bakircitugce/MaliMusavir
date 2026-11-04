from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import BankTransaction, JournalEntry, JournalLine
from app.bank.identifiers import normalize_iban


@dataclass(frozen=True)
class ReconciliationSummary:
    transaction_count: int
    unmatched_count: int
    matched_count: int
    ignored_count: int
    unmatched_amount: Decimal
    matched_amount: Decimal


@dataclass(frozen=True)
class POSAgingBucket:
    label: str
    transaction_count: int
    amount: Decimal


@dataclass(frozen=True)
class POSAgingSummary:
    as_of_date: date
    total_count: int
    total_amount: Decimal
    buckets: tuple[POSAgingBucket, ...]


async def summarize_pos_aging(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    as_of_date: date,
) -> POSAgingSummary:
    matched_entry_ids = select(BankTransaction.matched_journal_entry_id).where(
        BankTransaction.buro_id == buro_id,
        BankTransaction.mukellef_id == mukellef_id,
        BankTransaction.reconciliation_status == "matched",
        BankTransaction.matched_journal_entry_id.is_not(None),
    )
    statement = (
        select(
            JournalEntry.entry_date,
            JournalLine.credit,
        )
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .where(
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
            JournalEntry.status == "approved",
            JournalEntry.id.not_in(matched_entry_ids),
            JournalLine.account_code.like("108%"),
            JournalLine.credit > 0,
        )
    )
    rows = (await session.execute(statement)).all()
    buckets = {
        "0-30": [0, Decimal("0.00")],
        "31-60": [0, Decimal("0.00")],
        "61+": [0, Decimal("0.00")],
    }
    for entry_date, amount in rows:
        age = max(0, (as_of_date - entry_date).days)
        label = "0-30" if age <= 30 else "31-60" if age <= 60 else "61+"
        buckets[label][0] += 1
        buckets[label][1] += Decimal(str(amount))
    result_buckets = tuple(
        POSAgingBucket(label, values[0], values[1])
        for label, values in buckets.items()
    )
    return POSAgingSummary(
        as_of_date=as_of_date,
        total_count=sum(item.transaction_count for item in result_buckets),
        total_amount=sum((item.amount for item in result_buckets), Decimal("0.00")),
        buckets=result_buckets,
    )


async def list_reconciliation_queue(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    bank_account_iban: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 100,
) -> list[BankTransaction]:
    if limit < 1 or limit > 1000:
        raise ValueError("Mutabakat kuyruğu limiti 1 ile 1000 arasında olmalıdır.")
    statement = select(BankTransaction).where(
        BankTransaction.buro_id == buro_id,
        BankTransaction.mukellef_id == mukellef_id,
        BankTransaction.reconciliation_status == "unmatched",
    )
    if bank_account_iban is not None:
        statement = statement.where(
            BankTransaction.bank_account_iban == normalize_iban(bank_account_iban)
        )
    if start_date is not None:
        statement = statement.where(BankTransaction.transaction_date >= start_date)
    if end_date is not None:
        statement = statement.where(BankTransaction.transaction_date <= end_date)
    statement = statement.order_by(
        BankTransaction.transaction_date,
        BankTransaction.created_at,
    ).limit(limit)
    result = await session.execute(statement)
    return list(result.scalars().all())


async def summarize_reconciliation(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    bank_account_iban: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> ReconciliationSummary:
    statement = select(
        BankTransaction.reconciliation_status,
        func.count(BankTransaction.id),
        func.coalesce(func.sum(BankTransaction.debit + BankTransaction.credit), 0),
    ).where(
        BankTransaction.buro_id == buro_id,
        BankTransaction.mukellef_id == mukellef_id,
    )
    if bank_account_iban is not None:
        statement = statement.where(
            BankTransaction.bank_account_iban == normalize_iban(bank_account_iban)
        )
    if start_date is not None:
        statement = statement.where(BankTransaction.transaction_date >= start_date)
    if end_date is not None:
        statement = statement.where(BankTransaction.transaction_date <= end_date)
    statement = statement.group_by(BankTransaction.reconciliation_status)

    counts = {
        status: (count, Decimal(str(amount)))
        for status, count, amount in (await session.execute(statement)).all()
    }

    def values(status: str) -> tuple[int, Decimal]:
        return counts.get(status, (0, Decimal("0.00")))

    unmatched_count, unmatched_amount = values("unmatched")
    matched_count, matched_amount = values("matched")
    ignored_count, _ = values("ignored")
    return ReconciliationSummary(
        transaction_count=unmatched_count + matched_count + ignored_count,
        unmatched_count=unmatched_count,
        matched_count=matched_count,
        ignored_count=ignored_count,
        unmatched_amount=unmatched_amount,
        matched_amount=matched_amount,
    )
