from datetime import date
from decimal import Decimal, InvalidOperation
import re

from app.bank.transactions import BankTransactionInput
from app.ledger.draft import LedgerValidationError


_STATEMENT_DATE = re.compile(r"^\d{6}")


def _parse_amount(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise LedgerValidationError(f"Geçersiz MT940 tutarı: {value}") from exc


def parse_bank_mt940(
    text: str,
    *,
    bank_account_iban: str,
) -> list[BankTransactionInput]:
    if not bank_account_iban.strip():
        raise LedgerValidationError("Banka IBAN bilgisi boş olamaz.")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    transactions: list[BankTransactionInput] = []
    for index, line in enumerate(lines):
        if not line.startswith(":61:"):
            continue
        payload = line[4:]
        match = re.match(
            r"(?P<value_date>\d{6})(?:\d{4})?(?P<direction>[DC])(?P<amount>[0-9,]+)"
            r"(?:N[A-Z]{3})?(?P<reference>.*)",
            payload,
        )
        if match is None:
            raise LedgerValidationError(f"{index + 1}. satırda geçersiz MT940 :61: kaydı.")
        raw_date = match.group("value_date")
        transaction_date = date(
            2000 + int(raw_date[:2]),
            int(raw_date[2:4]),
            int(raw_date[4:6]),
        )
        amount = _parse_amount(match.group("amount"))
        direction = match.group("direction")
        description = ""
        if index + 1 < len(lines) and lines[index + 1].startswith(":86:"):
            description = lines[index + 1][4:].strip()
        external_id = match.group("reference").strip() or f"MT940-{index + 1}"
        transactions.append(
            BankTransactionInput(
                bank_account_iban=bank_account_iban,
                external_id=external_id,
                transaction_date=transaction_date,
                description=description or "MT940 banka hareketi",
                debit=amount if direction == "D" else Decimal("0.00"),
                credit=amount if direction == "C" else Decimal("0.00"),
            )
        )
    if not transactions:
        raise LedgerValidationError("MT940 dosyasında banka hareketi bulunamadı.")
    return transactions
