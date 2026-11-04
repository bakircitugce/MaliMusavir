from datetime import date
from decimal import Decimal
from uuid import UUID

from app.ledger.draft import JournalEntryDraft, JournalLine, LedgerValidationError, validate_balanced


def build_pos_settlement_entry(
    *,
    settlement_date: date,
    reference: str,
    gross_amount: Decimal,
    net_amount: Decimal,
    commission_amount: Decimal,
    bank_account_code: str,
    bank_account_name: str,
    commission_account_code: str,
    commission_account_name: str,
    pos_account_code: str,
    pos_account_name: str,
    source_document_id: UUID | None = None,
) -> JournalEntryDraft:
    if not reference.strip():
        raise LedgerValidationError("POS mahsup referansı boş olamaz.")
    if gross_amount < 0 or net_amount < 0 or commission_amount < 0:
        raise LedgerValidationError("POS mahsup tutarları negatif olamaz.")
    if net_amount + commission_amount != gross_amount:
        raise LedgerValidationError(
            f"POS mahsup tutarları dengeli değil: net+komisyon="
            f"{net_amount + commission_amount}, brüt={gross_amount}."
        )
    if net_amount == 0:
        raise LedgerValidationError("POS net yatışı sıfır olamaz.")

    entry = JournalEntryDraft(
        entry_date=settlement_date,
        description=f"POS mahsubu {reference}",
        lines=(
            JournalLine(bank_account_code, bank_account_name, debit=net_amount),
            JournalLine(
                commission_account_code,
                commission_account_name,
                debit=commission_amount,
            ),
            JournalLine(pos_account_code, pos_account_name, credit=gross_amount),
        ),
        source_document_id=source_document_id,
        rule_name="pos_settlement",
        rule_version="1",
        confidence=Decimal("0.90"),
    )
    return validate_balanced(entry)
