from decimal import Decimal

import pytest

from app.bank import parse_bank_mt940
from app.ledger import LedgerValidationError


def test_parse_bank_mt940_reads_debit_credit_and_description() -> None:
    rows = parse_bank_mt940(
        ":20:STATEMENT-001\n"
        ":61:2611031103D1200,00NTRFREF-001\n"
        ":86:POS KOMISYONU\n"
        ":61:2611031103C500,50NTRFREF-002\n"
        ":86:TAHSILAT\n",
        bank_account_iban="TR00",
    )

    assert len(rows) == 2
    assert rows[0].transaction_date.isoformat() == "2026-11-03"
    assert rows[0].debit == Decimal("1200.00")
    assert rows[0].credit == Decimal("0.00")
    assert rows[0].description == "POS KOMISYONU"
    assert rows[1].credit == Decimal("500.50")
    assert rows[1].external_id == "REF-002"


def test_parse_bank_mt940_rejects_empty_statement() -> None:
    with pytest.raises(LedgerValidationError, match="hareketi bulunamadı"):
        parse_bank_mt940(":20:EMPTY\n:62F:C260103TRY0,00", bank_account_iban="TR00")


def test_parse_bank_mt940_rejects_invalid_transaction() -> None:
    with pytest.raises(LedgerValidationError, match="geçersiz MT940"):
        parse_bank_mt940(":61:invalid", bank_account_iban="TR00")
