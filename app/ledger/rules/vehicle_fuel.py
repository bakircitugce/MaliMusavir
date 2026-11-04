from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from app.ledger.draft import JournalEntryDraft, JournalLine, LedgerValidationError, validate_balanced


CENT = Decimal("0.01")


def build_vehicle_fuel_entry(
    *,
    document_date: date,
    document_number: str,
    base_amount: Decimal,
    vat_amount: Decimal,
    total_amount: Decimal,
    deductible_ratio: Decimal,
    expense_account_code: str,
    expense_account_name: str,
    disallowed_expense_account_code: str,
    disallowed_expense_account_name: str,
    vat_account_code: str,
    vat_account_name: str,
    settlement_account_code: str,
    settlement_account_name: str,
    source_document_id: UUID | None = None,
) -> JournalEntryDraft:
    if not document_number.strip():
        raise LedgerValidationError("Yakıt belgesi numarası bulunmalıdır.")
    if not Decimal("0") <= deductible_ratio <= Decimal("1"):
        raise LedgerValidationError("Gider indirilebilirlik oranı 0 ile 1 arasında olmalıdır.")
    if base_amount < 0 or vat_amount < 0 or total_amount < 0:
        raise LedgerValidationError("Yakıt belgesi tutarları negatif olamaz.")
    if base_amount + vat_amount != total_amount:
        raise LedgerValidationError(
            f"Yakıt belgesi tutarları dengeli değil: matrah+KDV={base_amount + vat_amount}, "
            f"toplam={total_amount}."
        )

    deductible_base = (base_amount * deductible_ratio).quantize(CENT, rounding=ROUND_HALF_UP)
    disallowed_base = base_amount - deductible_base
    entry = JournalEntryDraft(
        entry_date=document_date,
        description=f"Binek araç yakıtı {document_number}",
        lines=(
            JournalLine(
                expense_account_code,
                expense_account_name,
                debit=deductible_base,
            ),
            JournalLine(
                disallowed_expense_account_code,
                disallowed_expense_account_name,
                debit=disallowed_base,
            ),
            JournalLine(vat_account_code, vat_account_name, debit=vat_amount),
            JournalLine(
                settlement_account_code,
                settlement_account_name,
                credit=total_amount,
            ),
        ),
        source_document_id=source_document_id,
        rule_name="vehicle_fuel",
        rule_version="1",
        confidence=Decimal("0.90"),
    )
    return validate_balanced(entry)
