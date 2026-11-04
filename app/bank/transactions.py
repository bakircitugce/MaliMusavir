from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.models import BankAccount, BankTransaction, JournalEntry, JournalLine
from app.bank.identifiers import normalize_iban
from app.ledger.draft import LedgerValidationError


@dataclass(frozen=True)
class BankTransactionInput:
    bank_account_iban: str
    external_id: str
    transaction_date: date
    description: str
    debit: Decimal
    credit: Decimal
    value_date: date | None = None
    counterparty_name: str | None = None
    counterparty_tax_id: str | None = None
    balance: Decimal | None = None


async def create_bank_transaction(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    transaction: BankTransactionInput,
) -> BankTransaction:
    iban = normalize_iban(transaction.bank_account_iban)
    if not transaction.external_id.strip():
        raise LedgerValidationError("Banka hareketi dış kimliği boş olamaz.")
    if not transaction.description.strip():
        raise LedgerValidationError("Banka hareketi açıklaması boş olamaz.")
    if transaction.debit < 0 or transaction.credit < 0:
        raise LedgerValidationError("Banka hareketi tutarları negatif olamaz.")
    if (transaction.debit > 0) == (transaction.credit > 0):
        raise LedgerValidationError("Banka hareketinde yalnızca borç veya alacak bulunmalıdır.")
    account_result = await session.execute(
        select(BankAccount.id).where(
            BankAccount.buro_id == buro_id,
            BankAccount.mukellef_id == mukellef_id,
            BankAccount.iban == iban,
            BankAccount.status == "active",
        )
    )
    if account_result.scalar_one_or_none() is None:
        raise LedgerValidationError("Aktif banka hesabı bulunamadı.")
    movement = BankTransaction(
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        bank_account_iban=iban,
        external_id=transaction.external_id,
        transaction_date=transaction.transaction_date,
        value_date=transaction.value_date,
        description=transaction.description,
        counterparty_name=transaction.counterparty_name,
        counterparty_tax_id=transaction.counterparty_tax_id,
        debit=transaction.debit,
        credit=transaction.credit,
        balance=transaction.balance,
        reconciliation_status="unmatched",
    )
    session.add(movement)
    await session.flush()
    return movement


async def list_bank_transactions(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    bank_account_iban: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    reconciliation_status: str | None = None,
) -> list[BankTransaction]:
    statement = select(BankTransaction).where(
        BankTransaction.buro_id == buro_id,
        BankTransaction.mukellef_id == mukellef_id,
    )
    if bank_account_iban is not None:
        statement = statement.where(BankTransaction.bank_account_iban == bank_account_iban)
    if start_date is not None:
        statement = statement.where(BankTransaction.transaction_date >= start_date)
    if end_date is not None:
        statement = statement.where(BankTransaction.transaction_date <= end_date)
    if reconciliation_status is not None:
        if reconciliation_status not in {"unmatched", "matched", "ignored"}:
            raise LedgerValidationError("Geçersiz mutabakat durumu.")
        statement = statement.where(
            BankTransaction.reconciliation_status == reconciliation_status
        )
    statement = statement.order_by(
        BankTransaction.transaction_date,
        BankTransaction.created_at,
    )
    result = await session.execute(statement)
    return list(result.scalars().all())


async def reconcile_bank_transaction(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    transaction_id: UUID,
    journal_entry_id: UUID,
) -> BankTransaction:
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
        raise LedgerValidationError("Banka hareketi zaten mutabık veya göz ardı edilmiş.")

    entry_result = await session.execute(
        select(JournalEntry).where(
            JournalEntry.id == journal_entry_id,
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
            JournalEntry.status == "approved",
        )
    )
    entry = entry_result.scalar_one_or_none()
    if entry is None:
        raise LedgerValidationError("Eşleştirilecek onaylı yevmiye fişi bulunamadı.")

    lines_result = await session.execute(
        select(JournalLine).where(
            JournalLine.journal_entry_id == entry.id,
            JournalLine.buro_id == buro_id,
            JournalLine.mukellef_id == mukellef_id,
        )
    )
    lines = lines_result.scalars().all()
    entry_total = sum((line.debit for line in lines), 0)
    transaction_amount = transaction.debit or transaction.credit
    if entry_total != transaction_amount:
        raise LedgerValidationError(
            f"Banka hareketi ve fiş tutarı eşleşmiyor: hareket={transaction_amount}, "
            f"fiş={entry_total}."
        )

    transaction.reconciliation_status = "matched"
    transaction.matched_journal_entry_id = entry.id
    await session.flush()
    return transaction


async def ignore_bank_transaction(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    transaction_id: UUID,
) -> BankTransaction:
    result = await session.execute(
        select(BankTransaction).where(
            BankTransaction.id == transaction_id,
            BankTransaction.buro_id == buro_id,
            BankTransaction.mukellef_id == mukellef_id,
        )
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        raise LedgerValidationError("Banka hareketi bulunamadı.")
    if transaction.reconciliation_status != "unmatched":
        raise LedgerValidationError("Yalnızca eşleşmemiş hareketler göz ardı edilebilir.")
    transaction.reconciliation_status = "ignored"
    await session.flush()
    return transaction
