"""Panelden yüklenen mizanı (Excel / CSV) okur.

Farklı muhasebe programlarının mizan çıktıları için başlık satırı otomatik bulunur
ve Türkçe sütun adları tanınır: Hesap Kodu / Kod, Hesap Adı, Borç, Alacak.

Mizan hiyerarşiktir (100 → 100.01 → 100.01.001); tüm satırları toplamak tutarları
birden çok kez sayar. Denge kontrolü bu yüzden şu sırayla yapılır:
1. Dosyada TOPLAM satırı varsa onun borç/alacak toplamı,
2. yoksa ana hesaplar (3 haneli kodlar),
3. o da yoksa alt hesabı olmayan (yaprak) hesaplar.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.bank.statement_parser import (
    _rows_from_csv,
    _rows_from_xlsx,
    normalize,
    parse_amount,
)
from app.chart.import_mizan import MizanRow

CODE_ALIASES = ("hesap kodu", "kod", "hesap no", "kodu", "hesap kod")
NAME_ALIASES = ("hesap adi", "hesap", "hesap ismi", "aciklama", "adi", "hesap adi aciklama")
DEBIT_ALIASES = ("borc", "borc toplami", "toplam borc", "borc tutari", "borclu")
CREDIT_ALIASES = ("alacak", "alacak toplami", "toplam alacak", "alacak tutari", "alacakli")
CODE_RE = re.compile(r"^\d{3}(?:[.\- ]?\d+)*$")


class MizanParseError(ValueError):
    pass


@dataclass
class MizanParseResult:
    rows: list[MizanRow]
    total_debit: Decimal
    total_credit: Decimal
    method: str
    warnings: list[str] = field(default_factory=list)

    @property
    def difference(self) -> Decimal:
        return (self.total_debit - self.total_credit).quantize(Decimal("0.01"))

    @property
    def balanced(self) -> bool:
        return self.difference == 0


def _col(headers: list[str], aliases: tuple[str, ...], used: set[int]) -> int | None:
    for alias in aliases:
        for i, h in enumerate(headers):
            if i not in used and h == alias:
                return i
    return None


def _code_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", "", str(value)).replace("-", ".")


def parse_mizan_file(content: bytes, filename: str = "") -> MizanParseResult:
    if not content:
        raise MizanParseError("Dosya boş.")
    if content[:2] == b"PK":
        rows = _rows_from_xlsx(content)
    elif content[:4] == b"%PDF":
        raise MizanParseError("Mizan için Excel (.xlsx) veya CSV dosyası yükleyin.")
    else:
        rows = _rows_from_csv(content)

    header_index = cols = None
    for index, row in enumerate(rows[:60]):
        headers = [normalize(c) for c in row]
        code = _col(headers, CODE_ALIASES, set())
        if code is None:
            continue
        used = {code}
        debit = _col(headers, DEBIT_ALIASES, used)
        credit = _col(headers, CREDIT_ALIASES, used | ({debit} if debit is not None else set()))
        if debit is None or credit is None:
            continue
        name = _col(headers, NAME_ALIASES, used | {debit, credit})
        header_index, cols = index, {"code": code, "name": name, "debit": debit, "credit": credit}
        break
    if header_index is None:
        raise MizanParseError(
            "Mizanda 'Hesap Kodu', 'Borç' ve 'Alacak' sütunları bulunamadı."
        )

    def cell(row: list[object], key: str) -> object:
        c = cols[key]
        return row[c] if c is not None and c < len(row) else None

    result_rows: list[MizanRow] = []
    reported: tuple[Decimal, Decimal] | None = None
    for row in rows[header_index + 1 :]:
        code = _code_text(cell(row, "code"))
        name = str(cell(row, "name") or "").strip()
        row_text = normalize(" ".join(str(c) for c in row if c is not None))
        if "toplam" in row_text.split() and not CODE_RE.match(code):
            debit = parse_amount(cell(row, "debit"))
            credit = parse_amount(cell(row, "credit"))
            if debit is not None and credit is not None:
                reported = (debit, credit)
            continue
        if not CODE_RE.match(code):
            continue
        result_rows.append(
            MizanRow(
                hesap_kodu=code,
                hesap_adi=name[:250] or code,
                borc=parse_amount(cell(row, "debit")) or Decimal("0.00"),
                alacak=parse_amount(cell(row, "credit")) or Decimal("0.00"),
            )
        )
    if not result_rows:
        raise MizanParseError("Mizanda hesap satırı bulunamadı.")

    codes = {r.hesap_kodu for r in result_rows}
    if len(codes) != len(result_rows):
        warnings = ["Aynı hesap kodu birden fazla satırda var; son satır geçerli sayılacak."]
    else:
        warnings = []

    if reported is not None:
        total_debit, total_credit, method = reported[0], reported[1], "TOPLAM satırı"
    else:
        main = [r for r in result_rows if re.fullmatch(r"\d{3}", r.hesap_kodu)]
        if main:
            pool, method = main, "ana hesaplar (3 haneli)"
        else:
            pool = [
                r for r in result_rows
                if not any(c != r.hesap_kodu and c.startswith(r.hesap_kodu + ".") for c in codes)
            ]
            method = "alt hesabı olmayan hesaplar"
        total_debit = sum((r.borc for r in pool), Decimal("0.00"))
        total_credit = sum((r.alacak for r in pool), Decimal("0.00"))
    result = MizanParseResult(
        rows=result_rows, total_debit=total_debit, total_credit=total_credit,
        method=method, warnings=warnings,
    )
    if not result.balanced:
        result.warnings.append(f"Mizan dengeli değil: borç-alacak farkı {result.difference}.")
    return result
