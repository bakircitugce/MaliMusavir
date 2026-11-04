from datetime import date
from decimal import Decimal
from uuid import UUID

from app.documents.extraction import InvoiceData
from app.ledger.draft import JournalEntryDraft, JournalLine, LedgerValidationError, validate_balanced


def build_purchase_invoice_entry(
    invoice: InvoiceData,
    *,
    expense_account_code: str,
    expense_account_name: str,
    vat_account_code: str,
    vat_account_name: str,
    supplier_account_code: str,
    supplier_account_name: str | None = None,
    source_document_id: UUID | None = None,
) -> JournalEntryDraft:
    if invoice.tax_exclusive_amount is None:
        raise LedgerValidationError("Alış faturasında matrah bulunmalıdır.")
    if invoice.tax_total is None:
        raise LedgerValidationError("Alış faturasında KDV tutarı bulunmalıdır.")
    if invoice.payable_amount is None:
        raise LedgerValidationError("Alış faturasında ödenecek tutar bulunmalıdır.")
    if not invoice.supplier.name and not supplier_account_name:
        raise LedgerValidationError("Satıcı adı bulunmalıdır.")

    base = invoice.tax_exclusive_amount
    vat = invoice.tax_total
    payable = invoice.payable_amount
    if base < 0 or vat < 0 or payable < 0:
        raise LedgerValidationError("Alış faturası tutarları negatif olamaz.")
    if base + vat != payable:
        raise LedgerValidationError(
            f"Alış faturası tutarları dengeli değil: matrah+KDV={base + vat}, "
            f"ödenecek={payable}."
        )

    try:
        entry_date = date.fromisoformat(invoice.issue_date)
    except ValueError as exc:
        raise LedgerValidationError("Fatura tarihi ISO formatında olmalıdır.") from exc

    entry = JournalEntryDraft(
        entry_date=entry_date,
        description=f"Alış faturası {invoice.invoice_id}",
        lines=(
            JournalLine(expense_account_code, expense_account_name, debit=base),
            JournalLine(vat_account_code, vat_account_name, debit=vat),
            JournalLine(
                supplier_account_code,
                supplier_account_name or invoice.supplier.name or "Satıcı",
                credit=payable,
            ),
        ),
        source_document_id=source_document_id,
        rule_name="purchase_invoice",
        rule_version="1",
        confidence=Decimal("0.95"),
    )
    return validate_balanced(entry)
