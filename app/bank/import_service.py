from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bank.transactions import BankTransactionInput, create_bank_transaction
from app.core.models import BankTransaction
from app.ledger.draft import LedgerValidationError


@dataclass(frozen=True)
class BankImportResult:
    created_count: int
    skipped_count: int


async def import_bank_transactions(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    transactions: list[BankTransactionInput],
) -> BankImportResult:
    if not transactions:
        raise LedgerValidationError("İçe aktarılacak banka hareketi bulunamadı.")

    transaction_keys = [
        (item.bank_account_iban, item.external_id) for item in transactions
    ]
    if len(set(transaction_keys)) != len(transaction_keys):
        raise LedgerValidationError(
            "İçe aktarma listesinde aynı IBAN ve dış hareket ID'si tekrar ediyor."
        )

    existing_result = await session.execute(
        select(BankTransaction.bank_account_iban, BankTransaction.external_id).where(
            BankTransaction.buro_id == buro_id,
            BankTransaction.mukellef_id == mukellef_id,
            BankTransaction.external_id.in_([item.external_id for item in transactions]),
        )
    )
    existing_keys = set(existing_result.all())
    created_count = 0
    for transaction in transactions:
        if (transaction.bank_account_iban, transaction.external_id) in existing_keys:
            continue
        await create_bank_transaction(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            transaction=transaction,
        )
        created_count += 1
    return BankImportResult(
        created_count=created_count,
        skipped_count=len(transactions) - created_count,
    )
