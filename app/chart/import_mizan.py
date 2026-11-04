from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable
from pathlib import Path

from openpyxl import load_workbook


@dataclass(frozen=True)
class MizanRow:
    hesap_kodu: str
    hesap_adi: str
    borc: Decimal
    alacak: Decimal


class MizanImportError(ValueError):
    pass


def parse_excel_amount(value: object) -> Decimal:
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    return parse_turkish_decimal(str(value or ""))


def parse_turkish_decimal(value: str) -> Decimal:
    cleaned = value.strip().replace(" ", "").replace("₺", "")
    if not cleaned:
        return Decimal("0.00")
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise MizanImportError(f"Geçersiz tutar: {value!r}") from exc


def parse_mizan_rows(rows: Iterable[dict[str, str]]) -> list[MizanRow]:
    result: list[MizanRow] = []
    for row in rows:
        code = (row.get("hesap_kodu") or row.get("kod") or "").strip()
        name = (row.get("hesap_adi") or row.get("hesap") or "").strip()
        if not code or code.upper().startswith("TOPLAM"):
            continue
        if not re.fullmatch(r"\d+(?:\.\d+)*", code):
            continue
        result.append(
            MizanRow(
                hesap_kodu=code,
                hesap_adi=name,
                borc=parse_turkish_decimal(row.get("borç", row.get("borc", ""))),
                alacak=parse_turkish_decimal(row.get("alacak", "")),
            )
        )
    return result


def validate_mizan_balance(rows: Iterable[MizanRow]) -> Decimal:
    difference = sum((row.borc - row.alacak for row in rows), Decimal("0.00"))
    difference = difference.quantize(Decimal("0.01"))
    if difference != Decimal("0.00"):
        raise MizanImportError(f"Mizan dengeli değil: fark {difference}")
    return difference


def import_mizan_workbook(path: str | Path) -> tuple[list[MizanRow], Decimal]:
    """Import the fixed report layout without treating group rows as headers."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    rows: list[MizanRow] = []
    reported_borc: Decimal | None = None
    reported_alacak: Decimal | None = None
    for values in sheet.iter_rows(min_row=8, values_only=True):
        label = str(values[2]).strip().upper() if values[2] is not None else ""
        if label.startswith("TOPLAM"):
            reported_borc = parse_excel_amount(values[9])
            reported_alacak = parse_excel_amount(values[10])
            continue
        code = str(values[1]).strip() if values[1] is not None else ""
        name = str(values[2]).strip() if values[2] is not None else ""
        if not code or not re.fullmatch(r"\d+(?:\.\d+)*", code):
            continue
        rows.append(
            MizanRow(
                hesap_kodu=code,
                hesap_adi=name,
                borc=parse_excel_amount(values[9]),
                alacak=parse_excel_amount(values[10]),
            )
        )
    if reported_borc is None or reported_alacak is None:
        raise MizanImportError("Mizan TOPLAM satırı bulunamadı")
    difference = (reported_borc - reported_alacak).quantize(Decimal("0.01"))
    if difference != Decimal("0.00"):
        raise MizanImportError(f"Mizan dengeli değil: fark {difference}")
    return rows, difference
