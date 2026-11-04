from datetime import date
from decimal import Decimal

import pytest

from app.ledger import LedgerValidationError
from app.ledger.rules import build_pos_receipt_entry


def test_pos_receipt_rule_creates_balanced_entry() -> None:
    entry = build_pos_receipt_entry(
        document_date=date(2026, 11, 3),
        receipt_number="OKC-42",
        base_amount=Decimal("100.00"),
        vat_amount=Decimal("20.00"),
        total_amount=Decimal("120.00"),
        expense_account_code="770.01.001",
        expense_account_name="Genel Yönetim Gideri",
        vat_account_code="191.03.001",
        vat_account_name="İndirilecek KDV %20",
        settlement_account_code="100.01.001",
        settlement_account_name="Merkez Kasa",
    )

    assert entry.total_debit == Decimal("120.00")
    assert entry.total_credit == Decimal("120.00")
    assert entry.rule_name == "pos_receipt"


def test_pos_receipt_rule_rejects_mismatched_total() -> None:
    with pytest.raises(LedgerValidationError, match="dengeli değil"):
        build_pos_receipt_entry(
            document_date=date(2026, 11, 3),
            receipt_number="OKC-42",
            base_amount=Decimal("100.00"),
            vat_amount=Decimal("20.00"),
            total_amount=Decimal("121.00"),
            expense_account_code="770.01.001",
            expense_account_name="Genel Yönetim Gideri",
            vat_account_code="191.03.001",
            vat_account_name="İndirilecek KDV %20",
            settlement_account_code="100.01.001",
            settlement_account_name="Merkez Kasa",
        )


def test_pos_receipt_rule_requires_receipt_number() -> None:
    with pytest.raises(LedgerValidationError, match="fiş numarası"):
        build_pos_receipt_entry(
            document_date=date(2026, 11, 3),
            receipt_number=" ",
            base_amount=Decimal("100.00"),
            vat_amount=Decimal("20.00"),
            total_amount=Decimal("120.00"),
            expense_account_code="770.01.001",
            expense_account_name="Genel Yönetim Gideri",
            vat_account_code="191.03.001",
            vat_account_name="İndirilecek KDV %20",
            settlement_account_code="100.01.001",
            settlement_account_name="Merkez Kasa",
        )
