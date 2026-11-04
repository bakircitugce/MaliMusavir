from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bank import POSSettlementInput, build_pos_settlement_from_bank_transaction
from app.ledger import LedgerValidationError


def make_transaction(status: str = "matched"):
    return SimpleNamespace(
        reconciliation_status=status,
        debit=Decimal("7092.00"),
        credit=Decimal("0.00"),
        transaction_date=date(2026, 11, 3),
        external_id="POS-BANK-001",
    )


def make_settlement() -> POSSettlementInput:
    return POSSettlementInput(
        gross_amount=Decimal("7200.00"),
        commission_amount=Decimal("108.00"),
        bank_account_code="102.01.001",
        bank_account_name="Banka",
        commission_account_code="770.03.001",
        commission_account_name="POS Komisyonu",
        pos_account_code="108.01.001",
        pos_account_name="POS",
    )


def test_pos_service_builds_draft_from_matched_bank_transaction() -> None:
    entry = build_pos_settlement_from_bank_transaction(
        make_transaction(),
        settlement=make_settlement(),
    )

    assert entry.total_debit == Decimal("7200.00")
    assert entry.total_credit == Decimal("7200.00")
    assert entry.source_document_id is None


def test_pos_service_rejects_unmatched_bank_transaction() -> None:
    with pytest.raises(LedgerValidationError, match="mutabık"):
        build_pos_settlement_from_bank_transaction(
            make_transaction("unmatched"),
            settlement=make_settlement(),
        )
