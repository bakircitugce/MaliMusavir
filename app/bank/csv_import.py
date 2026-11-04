import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import TextIOBase
from typing import BinaryIO

from openpyxl import load_workbook

from app.bank.transactions import BankTransactionInput
from app.ledger.draft import LedgerValidationError


REQUIRED_COLUMNS = {
    "external_id",
    "transaction_date",
    "description",
    "debit",
    "credit",
}


def _amount(value: object) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    try:
        text = str(value).strip()
        return Decimal(text.replace(".", "").replace(",", ".")).quantize(
            Decimal("0.01")
        )
    except InvalidOperation as exc:
        raise LedgerValidationError(f"Geçersiz banka tutarı: {value}") from exc


def _parse_row(
    row: dict[str, object],
    *,
    bank_account_iban: str,
    row_number: int,
) -> BankTransactionInput:
    raw_date = row.get("transaction_date")
    if isinstance(raw_date, datetime):
        transaction_date = raw_date.date()
    elif isinstance(raw_date, date):
        transaction_date = raw_date
    else:
        try:
            transaction_date = date.fromisoformat(str(raw_date or "").strip())
        except ValueError as exc:
            raise LedgerValidationError(
                f"{row_number}. satırda geçersiz banka işlem tarihi."
            ) from exc
    debit = _amount(row.get("debit"))
    credit = _amount(row.get("credit"))
    if (debit > 0) == (credit > 0):
        raise LedgerValidationError(
            f"{row_number}. satırda yalnızca borç veya alacak bulunmalıdır."
        )
    return BankTransactionInput(
        bank_account_iban=bank_account_iban,
        external_id=str(row.get("external_id") or "").strip(),
        transaction_date=transaction_date,
        description=str(row.get("description") or "").strip(),
        debit=debit,
        credit=credit,
        counterparty_name=str(row.get("counterparty_name") or "").strip() or None,
        counterparty_tax_id=str(row.get("counterparty_tax_id") or "").strip() or None,
        balance=_amount(row["balance"]) if row.get("balance") not in (None, "") else None,
    )


def parse_bank_csv(
    source: TextIOBase,
    *,
    bank_account_iban: str,
) -> list[BankTransactionInput]:
    if not bank_account_iban.strip():
        raise LedgerValidationError("Banka IBAN bilgisi boş olamaz.")
    sample = source.read(4096)
    source.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(source, dialect=dialect)
    if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
        raise LedgerValidationError(
            "Banka CSV başlıkları eksik: external_id, transaction_date, "
            "description, debit, credit gereklidir."
        )

    transactions: list[BankTransactionInput] = []
    for row_number, row in enumerate(reader, start=2):
        transactions.append(
            _parse_row(row, bank_account_iban=bank_account_iban, row_number=row_number)
        )
    return transactions


def parse_bank_xlsx(
    source: BinaryIO,
    *,
    bank_account_iban: str,
) -> list[BankTransactionInput]:
    if not bank_account_iban.strip():
        raise LedgerValidationError("Banka IBAN bilgisi boş olamaz.")
    workbook = load_workbook(source, read_only=True, data_only=True)
    worksheet = workbook.active
    rows = worksheet.iter_rows(values_only=True)
    headers = next(rows, None)
    if headers is None:
        raise LedgerValidationError("Banka XLSX dosyasında başlık satırı bulunamadı.")
    normalized_headers = [str(header or "").strip() for header in headers]
    if not REQUIRED_COLUMNS.issubset(normalized_headers):
        raise LedgerValidationError(
            "Banka XLSX başlıkları eksik: external_id, transaction_date, "
            "description, debit, credit gereklidir."
        )
    transactions: list[BankTransactionInput] = []
    for row_number, values in enumerate(rows, start=2):
        row = dict(zip(normalized_headers, values, strict=False))
        transactions.append(
            _parse_row(row, bank_account_iban=bank_account_iban, row_number=row_number)
        )
    return transactions
