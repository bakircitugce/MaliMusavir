from datetime import date
from decimal import Decimal
from uuid import UUID

from app.ledger.draft import JournalEntryDraft, JournalLine, LedgerValidationError, validate_balanced


def build_pos_receipt_entry(
    *,
    document_date: date,
    receipt_number: str,
    base_amount: Decimal,
    vat_amount: Decimal,
    total_amount: Decimal,
    expense_account_code: str,
    expense_account_name: str,
    vat_account_code: str,
    vat_account_name: str,
    settlement_account_code: str,
    settlement_account_name: str,
    source_document_id: UUID | None = None,
) -> JournalEntryDraft:
    if not receipt_number.strip():
        raise LedgerValidationError("ÖKC fiş numarası bulunmalıdır.")
    if base_amount < 0 or vat_amount < 0 or total_amount < 0:
        raise LedgerValidationError("ÖKC fişi tutarları negatif olamaz.")
    if base_amount + vat_amount != total_amount:
        raise LedgerValidationError(
            f"ÖKC fişi tutarları dengeli değil: matrah+KDV={base_amount + vat_amount}, "
            f"toplam={total_amount}."
        )

    entry = JournalEntryDraft(
        entry_date=document_date,
        description=f"ÖKC fişi {receipt_number}",
        lines=(
            JournalLine(expense_account_code, expense_account_name, debit=base_amount),
            JournalLine(vat_account_code, vat_account_name, debit=vat_amount),
            JournalLine(
                settlement_account_code,
                settlement_account_name,
                credit=total_amount,
            ),
        ),
        source_document_id=source_document_id,
        rule_name="pos_receipt",
        rule_version="1",
        confidence=Decimal("0.90"),
    )
    return validate_balanced(entry)
