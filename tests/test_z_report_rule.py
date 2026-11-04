from decimal import Decimal

import pytest

from app.documents.z_report import parse_z_report
from app.ledger import LedgerValidationError
from app.ledger.rules import build_z_report_entry


def make_report():
    return parse_z_report(
        "Z RAPORU 2026-11-03 Z NO: 42 ÖKC SİCİL NO: DEMO123 "
        "TOPLAM: 1.200,00 NAKİT: 500,00 KREDİ KARTI: 700,00"
    )


def test_z_report_rule_creates_balanced_entry() -> None:
    entry = build_z_report_entry(
        make_report(),
        revenue_account_code="600.03.001",
        revenue_account_name="Yurtiçi Satışlar",
        vat_account_code="391.03.001",
        vat_account_name="Hesaplanan KDV",
        revenue_base=Decimal("1000.00"),
        vat_amount=Decimal("200.00"),
        cash_account_code="100.01.001",
        card_account_code="108.01.001",
    )

    assert entry.total_debit == Decimal("1200.00")
    assert entry.total_credit == Decimal("1200.00")
    assert [line.account_code for line in entry.lines] == [
        "100.01.001",
        "108.01.001",
        "600.03.001",
        "391.03.001",
    ]


def test_z_report_rule_rejects_unbalanced_payment_split() -> None:
    report = parse_z_report(
        "Z RAPORU 2026-11-03 Z NO: 42 TOPLAM: 1.200,00 "
        "NAKİT: 400,00 KREDİ KARTI: 700,00"
    )

    with pytest.raises(LedgerValidationError, match="ödeme dağılımı"):
        build_z_report_entry(
            report,
            revenue_account_code="600.03.001",
            revenue_account_name="Yurtiçi Satışlar",
            vat_account_code="391.03.001",
            vat_account_name="Hesaplanan KDV",
            revenue_base=Decimal("1000.00"),
            vat_amount=Decimal("200.00"),
            cash_account_code="100.01.001",
            card_account_code="108.01.001",
        )


def test_z_report_rule_requires_account_for_present_payment_type() -> None:
    with pytest.raises(LedgerValidationError, match="POS"):
        build_z_report_entry(
            make_report(),
            revenue_account_code="600.03.001",
            revenue_account_name="Yurtiçi Satışlar",
            vat_account_code="391.03.001",
            vat_account_name="Hesaplanan KDV",
            revenue_base=Decimal("1000.00"),
            vat_amount=Decimal("200.00"),
            cash_account_code="100.01.001",
        )
