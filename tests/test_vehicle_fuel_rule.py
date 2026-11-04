from datetime import date
from decimal import Decimal

import pytest

from app.ledger import LedgerValidationError
from app.ledger.rules import build_vehicle_fuel_entry


def test_vehicle_fuel_rule_splits_deductible_and_disallowed_base() -> None:
    entry = build_vehicle_fuel_entry(
        document_date=date(2026, 11, 3),
        document_number="YAK-42",
        base_amount=Decimal("1000.00"),
        vat_amount=Decimal("200.00"),
        total_amount=Decimal("1200.00"),
        deductible_ratio=Decimal("0.70"),
        expense_account_code="770.01.001",
        expense_account_name="Akaryakıt Gideri",
        disallowed_expense_account_code="689.01.001",
        disallowed_expense_account_name="KKEG - Binek",
        vat_account_code="191.03.001",
        vat_account_name="İndirilecek KDV %20",
        settlement_account_code="320.01.001",
        settlement_account_name="Akaryakıt Tedarikçisi",
    )

    assert entry.total_debit == Decimal("1200.00")
    assert entry.total_credit == Decimal("1200.00")
    assert entry.lines[0].debit == Decimal("700.00")
    assert entry.lines[1].debit == Decimal("300.00")
    assert entry.rule_name == "vehicle_fuel"


def test_vehicle_fuel_rule_rejects_invalid_ratio() -> None:
    with pytest.raises(LedgerValidationError, match="oranı"):
        build_vehicle_fuel_entry(
            document_date=date(2026, 11, 3),
            document_number="YAK-42",
            base_amount=Decimal("1000.00"),
            vat_amount=Decimal("200.00"),
            total_amount=Decimal("1200.00"),
            deductible_ratio=Decimal("1.10"),
            expense_account_code="770.01.001",
            expense_account_name="Akaryakıt Gideri",
            disallowed_expense_account_code="689.01.001",
            disallowed_expense_account_name="KKEG - Binek",
            vat_account_code="191.03.001",
            vat_account_name="İndirilecek KDV %20",
            settlement_account_code="320.01.001",
            settlement_account_name="Tedarikçi",
        )


def test_vehicle_fuel_rule_rejects_mismatched_total() -> None:
    with pytest.raises(LedgerValidationError, match="dengeli değil"):
        build_vehicle_fuel_entry(
            document_date=date(2026, 11, 3),
            document_number="YAK-42",
            base_amount=Decimal("1000.00"),
            vat_amount=Decimal("200.00"),
            total_amount=Decimal("1201.00"),
            deductible_ratio=Decimal("0.70"),
            expense_account_code="770.01.001",
            expense_account_name="Akaryakıt Gideri",
            disallowed_expense_account_code="689.01.001",
            disallowed_expense_account_name="KKEG - Binek",
            vat_account_code="191.03.001",
            vat_account_name="İndirilecek KDV %20",
            settlement_account_code="320.01.001",
            settlement_account_name="Tedarikçi",
        )
