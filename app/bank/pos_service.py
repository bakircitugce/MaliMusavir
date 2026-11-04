from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.bank.pos import build_pos_settlement_entry
from app.core.models import BankTransaction
from app.ledger.draft import JournalEntryDraft, LedgerValidationError


@dataclass(frozen=True)
class POSSettlementInput:
    gross_amount: Decimal
    commission_amount: Decimal
    bank_account_code: str
    bank_account_name: str
    commission_account_code: str
    commission_account_name: str
    pos_account_code: str
    pos_account_name: str


def build_pos_settlement_from_bank_transaction(
    transaction: BankTransaction,
    *,
    settlement: POSSettlementInput,
    source_document_id: UUID | None = None,
) -> JournalEntryDraft:
    if transaction.reconciliation_status != "matched":
        raise LedgerValidationError("POS mahsup taslağı için banka hareketi mutabık olmalıdır.")
    if transaction.debit <= 0:
        raise LedgerValidationError("POS mahsup banka hareketi borç yönünde olmalıdır.")

    return build_pos_settlement_entry(
        settlement_date=transaction.transaction_date,
        reference=transaction.external_id,
        gross_amount=settlement.gross_amount,
        net_amount=transaction.debit,
        commission_amount=settlement.commission_amount,
        bank_account_code=settlement.bank_account_code,
        bank_account_name=settlement.bank_account_name,
        commission_account_code=settlement.commission_account_code,
        commission_account_name=settlement.commission_account_name,
        pos_account_code=settlement.pos_account_code,
        pos_account_name=settlement.pos_account_name,
        source_document_id=source_document_id,
    )
