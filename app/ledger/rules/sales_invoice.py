from datetime import date
from decimal import Decimal
from uuid import UUID

from app.documents.extraction import InvoiceData
from app.ledger.draft import JournalEntryDraft, JournalLine, LedgerValidationError, validate_balanced


def build_sales_invoice_entry(
    invoice: InvoiceData,
    *,
    revenue_account_code: str,
    revenue_account_name: str,
    vat_account_code: str,
    vat_account_name: str,
    settlement_account_code: str,
    settlement_account_name: str,
    source_document_id: UUID | None = None,
) -> JournalEntryDraft:
    if invoice.tax_exclusive_amount is None:
        raise LedgerValidationError("Satış faturasında matrah bulunmalıdır.")
    if invoice.tax_total is None:
        raise LedgerValidationError("Satış faturasında KDV tutarı bulunmalıdır.")
    if invoice.payable_amount is None:
        raise LedgerValidationError("Satış faturasında tahsil edilecek tutar bulunmalıdır.")
    if not invoice.customer.name:
        raise LedgerValidationError("Alıcı adı bulunmalıdır.")

    base = invoice.tax_exclusive_amount
    vat = invoice.tax_total
    payable = invoice.payable_amount
    if base < 0 or vat < 0 or payable < 0:
        raise LedgerValidationError("Satış faturası tutarları negatif olamaz.")
    if base + vat != payable:
        raise LedgerValidationError(
            f"Satış faturası tutarları dengeli değil: matrah+KDV={base + vat}, "
            f"tahsil edilecek={payable}."
        )

    try:
        entry_date = date.fromisoformat(invoice.issue_date)
    except ValueError as exc:
        raise LedgerValidationError("Fatura tarihi ISO formatında olmalıdır.") from exc

    entry = JournalEntryDraft(
        entry_date=entry_date,
        description=f"Satış faturası {invoice.invoice_id}",
        lines=(
            JournalLine(
                settlement_account_code,
                settlement_account_name,
                debit=payable,
            ),
            JournalLine(revenue_account_code, revenue_account_name, credit=base),
            JournalLine(vat_account_code, vat_account_name, credit=vat),
        ),
        source_document_id=source_document_id,
        rule_name="sales_invoice",
        rule_version="1",
        confidence=Decimal("0.95"),
    )
    return validate_balanced(entry)
