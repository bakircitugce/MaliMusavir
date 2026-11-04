from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import BankTransaction, JournalEntry, JournalLine
from app.ledger.draft import LedgerValidationError


@dataclass(frozen=True)
class ReconciliationCandidate:
    journal_entry_id: UUID
    entry_date: date
    description: str
    amount: Decimal
    date_distance: int
    score: int


@dataclass(frozen=True)
class ReconciliationGroupCandidate:
    journal_entry_ids: tuple[UUID, ...]
    entry_dates: tuple[date, ...]
    descriptions: tuple[str, ...]
    amount: Decimal
    max_date_distance: int
    score: int


async def list_reconciliation_group_candidates(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    transaction_id: UUID,
    date_window: int = 3,
    max_entries: int = 3,
    limit: int = 20,
) -> list[ReconciliationGroupCandidate]:
    if date_window < 0 or date_window > 31:
        raise ValueError("Mutabakat tarih aralığı 0 ile 31 gün arasında olmalıdır.")
    if max_entries < 2 or max_entries > 3:
        raise ValueError("Mutabakat grup büyüklüğü 2 ile 3 arasında olmalıdır.")
    if limit < 1 or limit > 100:
        raise ValueError("Mutabakat aday limiti 1 ile 100 arasında olmalıdır.")

    transaction_result = await session.execute(
        select(BankTransaction).where(
            BankTransaction.id == transaction_id,
            BankTransaction.buro_id == buro_id,
            BankTransaction.mukellef_id == mukellef_id,
        )
    )
    transaction = transaction_result.scalar_one_or_none()
    if transaction is None:
        raise LedgerValidationError("Banka hareketi bulunamadı.")
    if transaction.reconciliation_status != "unmatched":
        raise LedgerValidationError("Yalnızca eşleşmemiş hareketler için aday aranabilir.")

    matched_entry_ids = select(BankTransaction.matched_journal_entry_id).where(
        BankTransaction.buro_id == buro_id,
        BankTransaction.mukellef_id == mukellef_id,
        BankTransaction.reconciliation_status == "matched",
        BankTransaction.matched_journal_entry_id.is_not(None),
    )
    start_date = transaction.transaction_date - timedelta(days=date_window)
    end_date = transaction.transaction_date + timedelta(days=date_window)
    statement = (
        select(
            JournalEntry.id,
            JournalEntry.entry_date,
            JournalEntry.description,
            func.sum(JournalLine.debit).label("debit_amount"),
            func.sum(JournalLine.credit).label("credit_amount"),
        )
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .where(
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
            JournalEntry.status == "approved",
            JournalEntry.entry_date >= start_date,
            JournalEntry.entry_date <= end_date,
            JournalEntry.id.not_in(matched_entry_ids),
        )
        .group_by(JournalEntry.id, JournalEntry.entry_date, JournalEntry.description)
    )
    rows = (await session.execute(statement)).all()
    target_amount = transaction.debit or transaction.credit
    target_column = "debit_amount" if transaction.debit > 0 else "credit_amount"
    entries = [
        (
            entry_id,
            entry_date,
            description,
            Decimal(str(
                debit_amount if target_column == "debit_amount" else credit_amount
            )),
        )
        for entry_id, entry_date, description, debit_amount, credit_amount in rows
    ]
    candidates: list[ReconciliationGroupCandidate] = []
    for group_size in range(2, max_entries + 1):
        for group in combinations(entries, group_size):
            if sum((item[3] for item in group), Decimal("0")) != target_amount:
                continue
            distances = tuple(
                abs((item[1] - transaction.transaction_date).days) for item in group
            )
            candidates.append(
                ReconciliationGroupCandidate(
                    journal_entry_ids=tuple(item[0] for item in group),
                    entry_dates=tuple(item[1] for item in group),
                    descriptions=tuple(item[2] for item in group),
                    amount=target_amount,
                    max_date_distance=max(distances),
                    score=100 - (max(distances) * 10) - (group_size - 2) * 5,
                )
            )
    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            candidate.max_date_distance,
            tuple(str(item) for item in candidate.journal_entry_ids),
        )
    )
    return candidates[:limit]


async def list_reconciliation_candidates(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    transaction_id: UUID,
    date_window: int = 3,
    limit: int = 20,
) -> list[ReconciliationCandidate]:
    if date_window < 0 or date_window > 31:
        raise ValueError("Mutabakat tarih aralığı 0 ile 31 gün arasında olmalıdır.")
    if limit < 1 or limit > 100:
        raise ValueError("Mutabakat aday limiti 1 ile 100 arasında olmalıdır.")

    transaction_result = await session.execute(
        select(BankTransaction).where(
            BankTransaction.id == transaction_id,
            BankTransaction.buro_id == buro_id,
            BankTransaction.mukellef_id == mukellef_id,
        )
    )
    transaction = transaction_result.scalar_one_or_none()
    if transaction is None:
        raise LedgerValidationError("Banka hareketi bulunamadı.")
    if transaction.reconciliation_status != "unmatched":
        raise LedgerValidationError("Yalnızca eşleşmemiş hareketler için aday aranabilir.")

    amount = transaction.debit or transaction.credit
    start_date = transaction.transaction_date - timedelta(days=date_window)
    end_date = transaction.transaction_date + timedelta(days=date_window)
    matched_entry_ids = select(BankTransaction.matched_journal_entry_id).where(
        BankTransaction.buro_id == buro_id,
        BankTransaction.mukellef_id == mukellef_id,
        BankTransaction.reconciliation_status == "matched",
        BankTransaction.matched_journal_entry_id.is_not(None),
    )
    statement = (
        select(
            JournalEntry.id,
            JournalEntry.entry_date,
            JournalEntry.description,
            func.sum(JournalLine.debit).label("debit_amount"),
            func.sum(JournalLine.credit).label("credit_amount"),
        )
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .where(
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
            JournalEntry.status == "approved",
            JournalEntry.entry_date >= start_date,
            JournalEntry.entry_date <= end_date,
            JournalEntry.id.not_in(matched_entry_ids),
        )
        .group_by(JournalEntry.id, JournalEntry.entry_date, JournalEntry.description)
    )
    rows = (await session.execute(statement)).all()
    target_column = "debit_amount" if transaction.debit > 0 else "credit_amount"
    candidates = [
        ReconciliationCandidate(
            journal_entry_id=entry_id,
            entry_date=entry_date,
            description=description,
            amount=Decimal(str(
                debit_amount if target_column == "debit_amount" else credit_amount
            )),
            date_distance=abs((entry_date - transaction.transaction_date).days),
            score=100 - (abs((entry_date - transaction.transaction_date).days) * 10),
        )
        for entry_id, entry_date, description, debit_amount, credit_amount in rows
        if (debit_amount if target_column == "debit_amount" else credit_amount) == amount
    ]
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.entry_date, str(candidate.journal_entry_id)))
    return candidates[:limit]
