from io import StringIO
from io import BytesIO
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.bank import parse_bank_csv, parse_bank_xlsx
from app.ledger import LedgerValidationError


def test_parse_bank_csv_reads_turkish_amounts() -> None:
    rows = parse_bank_csv(
        StringIO(
            "external_id;transaction_date;description;debit;credit;balance\n"
            "CSV-001;2026-11-03;POS tahsilatı;1.200,00;;5.000,00\n"
        ),
        bank_account_iban="TR00",
    )

    assert len(rows) == 1
    assert rows[0].debit == Decimal("1200.00")
    assert rows[0].credit == Decimal("0.00")
    assert rows[0].balance == Decimal("5000.00")


def test_parse_bank_csv_rejects_missing_columns() -> None:
    with pytest.raises(LedgerValidationError, match="başlıkları eksik"):
        parse_bank_csv(StringIO("external_id;description\nCSV-001;test\n"), bank_account_iban="TR00")


def test_parse_bank_csv_rejects_both_directions() -> None:
    with pytest.raises(LedgerValidationError, match="yalnızca"):
        parse_bank_csv(
            StringIO(
                "external_id;transaction_date;description;debit;credit\n"
                "CSV-001;2026-11-03;test;10;10\n"
            ),
            bank_account_iban="TR00",
        )


def test_parse_bank_xlsx_reads_dates_and_numeric_amounts() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["external_id", "transaction_date", "description", "debit", "credit", "balance"])
    worksheet.append(["XLSX-001", date(2026, 11, 3), "POS tahsilatı", 1200, 0, 5000])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    rows = parse_bank_xlsx(output, bank_account_iban="TR00")

    assert len(rows) == 1
    assert rows[0].transaction_date == date(2026, 11, 3)
    assert rows[0].debit == Decimal("1200.00")
    assert rows[0].balance == Decimal("5000.00")


def test_parse_bank_xlsx_rejects_missing_columns() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["external_id", "description"])
    worksheet.append(["XLSX-002", "Eksik başlık"])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    with pytest.raises(LedgerValidationError, match="başlıkları eksik"):
        parse_bank_xlsx(output, bank_account_iban="TR00")
