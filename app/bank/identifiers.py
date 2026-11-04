import re

from app.ledger.draft import LedgerValidationError


def normalize_iban(value: str) -> str:
    normalized = re.sub(r"\s+", "", value).upper()
    if not normalized:
        raise LedgerValidationError("Banka IBAN bilgisi boş olamaz.")
    if len(normalized) > 34 or not re.fullmatch(r"[A-Z]{2}[0-9A-Z]+", normalized):
        raise LedgerValidationError("Banka IBAN bilgisi geçersiz.")
    return normalized
