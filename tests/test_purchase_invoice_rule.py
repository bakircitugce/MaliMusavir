from decimal import Decimal

import pytest

from app.documents.extraction import InvoiceData, PartyData
from app.ledger import LedgerValidationError
from app.ledger.rules import build_purchase_invoice_entry


def make_invoice(
    *,
    base: Decimal = Decimal("100.00"),
    vat: Decimal | None = Decimal("20.00"),
    payable: Decimal = Decimal("120.00"),
) -> InvoiceData:
    return InvoiceData(
        invoice_id="F-2026-001",
        issue_date="2026-11-03",
        invoice_type_code="ALIS",
        profile_id="TEMELFATURA",
        currency="TRY",
        supplier=PartyData("Örnek Tedarikçi", "1234567890", "VKN"),
        customer=PartyData("Örnek İşletme", "0987654321", "VKN"),
        tax_exclusive_amount=base,
        tax_inclusive_amount=payable,
        payable_amount=payable,
        tax_total=vat,
        lines=(),
    )


def test_purchase_invoice_rule_creates_balanced_entry() -> None:
    entry = build_purchase_invoice_entry(
        make_invoice(),
        expense_account_code="153.01.001",
        expense_account_name="Ticari Mallar",
        vat_account_code="191.03.001",
        vat_account_name="İndirilecek KDV %20",
        supplier_account_code="320.01.001",
    )

    assert entry.total_debit == Decimal("120.00")
    assert entry.total_credit == Decimal("120.00")
    assert entry.rule_name == "purchase_invoice"
    assert entry.lines[-1].account_name == "Örnek Tedarikçi"


def test_purchase_invoice_rule_rejects_mismatched_totals() -> None:
    with pytest.raises(LedgerValidationError, match="dengeli değil"):
        build_purchase_invoice_entry(
            make_invoice(payable=Decimal("121.00")),
            expense_account_code="153.01.001",
            expense_account_name="Ticari Mallar",
            vat_account_code="191.03.001",
            vat_account_name="İndirilecek KDV %20",
            supplier_account_code="320.01.001",
        )


def test_purchase_invoice_rule_requires_tax_amount() -> None:
    with pytest.raises(LedgerValidationError, match="KDV"):
        build_purchase_invoice_entry(
            make_invoice(vat=None),
            expense_account_code="153.01.001",
            expense_account_name="Ticari Mallar",
            vat_account_code="191.03.001",
            vat_account_name="İndirilecek KDV %20",
            supplier_account_code="320.01.001",
        )
