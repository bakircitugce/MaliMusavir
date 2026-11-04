from datetime import date
from decimal import Decimal

import pytest

from app.bank import build_pos_settlement_entry
from app.ledger import LedgerValidationError


def test_pos_settlement_rule_creates_balanced_entry() -> None:
    entry = build_pos_settlement_entry(
        settlement_date=date(2026, 11, 3),
        reference="POS-2026-001",
        gross_amount=Decimal("7200.00"),
        net_amount=Decimal("7092.00"),
        commission_amount=Decimal("108.00"),
        bank_account_code="102.01.001",
        bank_account_name="Banka",
        commission_account_code="770.03.001",
        commission_account_name="Banka POS Komisyonu",
        pos_account_code="108.01.001",
        pos_account_name="POS",
    )

    assert entry.total_debit == Decimal("7200.00")
    assert entry.total_credit == Decimal("7200.00")
    assert entry.lines[0].debit == Decimal("7092.00")
    assert entry.lines[1].debit == Decimal("108.00")
    assert entry.lines[2].credit == Decimal("7200.00")


def test_pos_settlement_rule_rejects_mismatched_amounts() -> None:
    with pytest.raises(LedgerValidationError, match="dengeli değil"):
        build_pos_settlement_entry(
            settlement_date=date(2026, 11, 3),
            reference="POS-2026-002",
            gross_amount=Decimal("7200.00"),
            net_amount=Decimal("7000.00"),
            commission_amount=Decimal("108.00"),
            bank_account_code="102.01.001",
            bank_account_name="Banka",
            commission_account_code="770.03.001",
            commission_account_name="Komisyon",
            pos_account_code="108.01.001",
            pos_account_name="POS",
        )
