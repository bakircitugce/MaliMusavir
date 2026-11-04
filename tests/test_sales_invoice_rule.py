from decimal import Decimal

import pytest

from app.documents.extraction import InvoiceData, PartyData
from app.ledger import LedgerValidationError
from app.ledger.rules import build_sales_invoice_entry


def make_invoice(
    *,
    base: Decimal = Decimal("100.00"),
    vat: Decimal | None = Decimal("20.00"),
    payable: Decimal = Decimal("120.00"),
) -> InvoiceData:
    return InvoiceData(
        invoice_id="S-2026-001",
        issue_date="2026-11-03",
        invoice_type_code="SATIS",
        profile_id="TEMELFATURA",
        currency="TRY",
        supplier=PartyData("Örnek İşletme", "1234567890", "VKN"),
        customer=PartyData("Örnek Müşteri", "0987654321", "VKN"),
        tax_exclusive_amount=base,
        tax_inclusive_amount=payable,
        payable_amount=payable,
        tax_total=vat,
        lines=(),
    )


def test_sales_invoice_rule_creates_balanced_entry() -> None:
    entry = build_sales_invoice_entry(
        make_invoice(),
        revenue_account_code="600.03.001",
        revenue_account_name="Yurtiçi Satışlar %20",
        vat_account_code="391.03.001",
        vat_account_name="Hesaplanan KDV %20",
        settlement_account_code="120.01.001",
        settlement_account_name="Örnek Müşteri",
    )

    assert entry.total_debit == Decimal("120.00")
    assert entry.total_credit == Decimal("120.00")
    assert entry.rule_name == "sales_invoice"
    assert entry.lines[0].debit == Decimal("120.00")


def test_sales_invoice_rule_rejects_mismatched_totals() -> None:
    with pytest.raises(LedgerValidationError, match="dengeli değil"):
        build_sales_invoice_entry(
            make_invoice(payable=Decimal("121.00")),
            revenue_account_code="600.03.001",
            revenue_account_name="Yurtiçi Satışlar %20",
            vat_account_code="391.03.001",
            vat_account_name="Hesaplanan KDV %20",
            settlement_account_code="120.01.001",
            settlement_account_name="Örnek Müşteri",
        )


def test_sales_invoice_rule_requires_customer() -> None:
    invoice = make_invoice()
    invoice = InvoiceData(
        invoice_id=invoice.invoice_id,
        issue_date=invoice.issue_date,
        invoice_type_code=invoice.invoice_type_code,
        profile_id=invoice.profile_id,
        currency=invoice.currency,
        supplier=invoice.supplier,
        customer=PartyData(None, None, None),
        tax_exclusive_amount=invoice.tax_exclusive_amount,
        tax_inclusive_amount=invoice.tax_inclusive_amount,
        payable_amount=invoice.payable_amount,
        tax_total=invoice.tax_total,
        lines=invoice.lines,
    )

    with pytest.raises(LedgerValidationError, match="Alıcı adı"):
        build_sales_invoice_entry(
            invoice,
            revenue_account_code="600.03.001",
            revenue_account_name="Yurtiçi Satışlar %20",
            vat_account_code="391.03.001",
            vat_account_name="Hesaplanan KDV %20",
            settlement_account_code="120.01.001",
            settlement_account_name="Alıcı",
        )
