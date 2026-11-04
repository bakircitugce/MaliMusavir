from datetime import date
from decimal import Decimal

import pytest

from app.bank import build_credit_card_settlement_entry
from app.ledger import LedgerValidationError


def test_credit_card_settlement_uses_309_and_balances() -> None:
    entry = build_credit_card_settlement_entry(
        settlement_date=date(2026, 11, 3),
        reference="CARD-001",
        gross_amount=Decimal("1000.00"),
        net_amount=Decimal("970.00"),
        commission_amount=Decimal("30.00"),
        bank_account_code="102.01.001",
        bank_account_name="Banka",
        commission_account_code="770.03.001",
        commission_account_name="POS komisyonu",
        credit_card_account_code="309.01.001",
        credit_card_account_name="Kredi kartları",
    )

    assert entry.rule_name == "credit_card_settlement"
    assert entry.description == "Kredi kartı mahsubu CARD-001"
    assert entry.total_debit == Decimal("1000.00")
    assert entry.total_credit == Decimal("1000.00")
    assert entry.lines[-1].account_code == "309.01.001"
    assert entry.lines[-1].credit == Decimal("1000.00")


def test_credit_card_settlement_rejects_unbalanced_amounts() -> None:
    with pytest.raises(LedgerValidationError, match="dengeli değil"):
        build_credit_card_settlement_entry(
            settlement_date=date(2026, 11, 3),
            reference="CARD-002",
            gross_amount=Decimal("1000.00"),
            net_amount=Decimal("970.00"),
            commission_amount=Decimal("20.00"),
            bank_account_code="102.01.001",
            bank_account_name="Banka",
            commission_account_code="770.03.001",
            commission_account_name="Komisyon",
            credit_card_account_code="309.01.001",
            credit_card_account_name="Kredi kartları",
        )
