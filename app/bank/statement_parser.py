"""Evrensel banka ekstresi okuyucu (Faz 2 / Adım 4).

Excel (xlsx), CSV ve metin tabanlı PDF ekstreleri tek giriş noktasından okunur:

- Tablo dosyalarında başlık satırı otomatik bulunur (üstteki hesap bilgisi blokları
  atlanır) ve Türkçe sütun adları tanınır: Tarih / Hareket Tarihi / İşlem Tarihi,
  Açıklama, Tutar / İşlem Tutarı (işaretli) veya Borç + Alacak, Bakiye, İşlem/Fiş/
  Dekont No.
- PDF'te her hareket tarih ile başlayan satırdan, "tutar TL bakiye TL" ile biten
  satıra kadar toplanır (çok satırlı açıklamalar desteklenir).
- IBAN dosyanın içinden okunur; yoksa çağıran tarafından verilmelidir.
- Bakiye zinciri ile doğrulama: (önceki bakiye + tutar = yeni bakiye). Tutmayan
  satırlar uyarı olarak raporlanır; bu, okuma hatalarını matematiksel olarak yakalar.

İşaret kuralı: pozitif tutar hesaba giren paradır (102 borç), negatif tutar çıkandır.
"""

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from app.documents.pdf import extract_pdf_text

CENT = Decimal("0.01")
IBAN_RE = re.compile(r"\bTR\s?\d{2}(?:\s?\d{4}){5}\s?\d{2}\b", re.I)
DATE_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})")
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b")

BANK_KEYWORDS = {
    "ziraat": "Ziraat Bankası",
    "vakif": "VakıfBank",
    "vb musteri": "VakıfBank",
    "yapi kredi": "Yapı Kredi",
    "yapikredi": "Yapı Kredi",
    "garanti": "Garanti BBVA",
    "is bankasi": "Türkiye İş Bankası",
    "isbank": "Türkiye İş Bankası",
    "akbank": "Akbank",
    "halkbank": "Halkbank",
    "halk bankasi": "Halkbank",
    "qnb": "QNB",
    "denizbank": "DenizBank",
    "kuveyt": "Kuveyt Türk",
    "teb": "TEB",
    "ing": "ING",
    "enpara": "Enpara",
}

# Sütun adı eşanlamlıları (normalize edilmiş: küçük harf, Türkçe karakter sadeleştirilmiş)
DATE_ALIASES = ("islem tarihi", "hareket tarih", "hareket tarihi", "tarih", "islem tarih")
DESC_ALIASES = ("aciklama", "aciklamalar", "islem aciklamasi", "detay")
TYPE_ALIASES = ("islem", "islem turu", "islem tipi")
AMOUNT_ALIASES = ("islem tutari", "tutar", "tutar tl", "miktar")
DEBIT_ALIASES = ("borc", "cikan", "giden", "borc tutari")  # hesaptan çıkan
CREDIT_ALIASES = ("alacak", "giren", "gelen", "alacak tutari")  # hesaba giren
BALANCE_ALIASES = ("bakiye", "kalan bakiye", "bakiye tl")
REF_ALIASES = ("islem no", "dekont no", "referans", "referans no", "ref no", "fis no", "fis no.")
TIME_ALIASES = ("saat", "islem saati")


class StatementParseError(ValueError):
    pass


@dataclass
class StatementLine:
    transaction_date: date
    description: str
    amount: Decimal  # + giriş, - çıkış
    balance: Decimal | None = None
    reference: str | None = None
    time: str | None = None
    external_id: str = ""


@dataclass
class ParsedStatement:
    source_format: str
    iban: str | None
    bank_name: str | None
    lines: list[StatementLine]
    balance_checked: int = 0
    balance_mismatches: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def period(self) -> tuple[date, date] | None:
        if not self.lines:
            return None
        dates = [line.transaction_date for line in self.lines]
        return min(dates), max(dates)


# --- Yardımcılar ------------------------------------------------------------


def normalize(text: object) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).replace("ı", "i").replace("İ", "i")
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_amount(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(CENT)
    text = str(value).strip().replace("TL", "").replace("TRY", "").replace("₺", "").strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(" ", "")
    if not text or not re.search(r"\d", text):
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        amount = Decimal(text).quantize(CENT)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    match = DATE_RE.search(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
    else:
        iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
        if not iso:
            return None
        year, month, day = (int(g) for g in iso.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def normalize_iban(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def detect_bank(*texts: str) -> str | None:
    haystack = " ".join(normalize(t) for t in texts if t)
    for key, name in BANK_KEYWORDS.items():
        if re.search(rf"\b{re.escape(key)}\b", haystack):
            return name
    return None


def _find_column(headers: list[str], aliases: tuple[str, ...], exclude: set[int]) -> int | None:
    for alias in aliases:  # önce birebir eşleşme, eşanlam sırası önceliklidir
        for index, header in enumerate(headers):
            if index not in exclude and header == alias:
                return index
    for alias in aliases:
        for index, header in enumerate(headers):
            if index not in exclude and header.startswith(alias + " "):
                return index
    return None


# --- Tablo (Excel / CSV) ----------------------------------------------------


def _rows_from_xlsx(content: bytes) -> list[list[object]]:
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        rows: list[list[object]] = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) > 200_000:
                    raise StatementParseError("Ekstre çok büyük (200.000 satır sınırı).")
            if rows:
                break  # ilk dolu sayfa
        return rows
    finally:
        workbook.close()


def _rows_from_csv(content: bytes) -> list[list[object]]:
    for encoding in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


def _parse_table(rows: list[list[object]], source_format: str) -> ParsedStatement:
    header_index = columns = None
    for index, row in enumerate(rows[:60]):
        headers = [normalize(cell) for cell in row]
        used: set[int] = set()
        date_col = _find_column(headers, DATE_ALIASES, used)
        if date_col is None:
            continue
        used.add(date_col)
        amount_col = _find_column(headers, AMOUNT_ALIASES, used)
        debit_col = credit_col = None
        if amount_col is None:
            debit_col = _find_column(headers, DEBIT_ALIASES, used)
            credit_col = _find_column(headers, CREDIT_ALIASES, used)
            if debit_col is None or credit_col is None:
                continue
        header_index = index
        cols_used = {date_col, amount_col, debit_col, credit_col} - {None}
        desc_col = _find_column(headers, DESC_ALIASES, cols_used)
        type_col = _find_column(headers, TYPE_ALIASES, cols_used | {desc_col} - {None})
        columns = {
            "date": date_col,
            "amount": amount_col,
            "debit": debit_col,
            "credit": credit_col,
            "desc": desc_col,
            "type": type_col,
            "balance": _find_column(headers, BALANCE_ALIASES, cols_used),
            "ref": _find_column(headers, REF_ALIASES, cols_used),
            "time": _find_column(headers, TIME_ALIASES, cols_used),
        }
        break
    if header_index is None:
        raise StatementParseError(
            "Ekstrede tarih ve tutar (veya borç/alacak) sütunları bulunamadı."
        )

    head_text = " ".join(str(c) for row in rows[: header_index + 1] for c in row if c)
    iban_match = IBAN_RE.search(head_text)
    statement = ParsedStatement(
        source_format=source_format,
        iban=normalize_iban(iban_match.group(0)) if iban_match else None,
        bank_name=detect_bank(head_text),
        lines=[],
    )

    def cell(row: list[object], key: str) -> object:
        col = columns[key]
        return row[col] if col is not None and col < len(row) else None

    for row in rows[header_index + 1 :]:
        if not any(c not in (None, "") for c in row):
            continue
        tx_date = parse_date(cell(row, "date"))
        if tx_date is None:
            continue  # alt bilgi / toplam satırları
        if columns["amount"] is not None:
            amount = parse_amount(cell(row, "amount"))
        else:
            debit = parse_amount(cell(row, "debit")) or Decimal("0")
            credit = parse_amount(cell(row, "credit")) or Decimal("0")
            amount = abs(credit) - abs(debit)
        if amount is None or amount == 0:
            continue
        parts = [str(cell(row, "type") or "").strip(), str(cell(row, "desc") or "").strip()]
        description = " - ".join(p for p in parts if p) or "Banka hareketi"
        # Saat; ayrı sütunda veya herhangi bir "tarih saat" hücresinde olabilir
        # (ör. VakıfBank: İŞLEM TARİHİ yalnızca tarih, HAREKET TARIH tarih+saat).
        time_match = TIME_RE.search(str(cell(row, "time") or ""))
        if time_match is None:
            for value in row:
                if isinstance(value, datetime) and (value.hour or value.minute):
                    time_match = TIME_RE.search(value.strftime("%H:%M"))
                    break
                if isinstance(value, str) and DATE_RE.search(value) and TIME_RE.search(value):
                    time_match = TIME_RE.search(value)
                    break
        ref = cell(row, "ref")
        statement.lines.append(
            StatementLine(
                transaction_date=tx_date,
                description=description[:500],
                amount=amount,
                balance=parse_amount(cell(row, "balance")),
                reference=str(ref).strip() if ref not in (None, "", 0) else None,
                time=time_match.group(1) if time_match else None,
            )
        )
    return statement


# --- PDF --------------------------------------------------------------------

_PDF_TX_START = re.compile(r"^\s*(\d{2}[./]\d{2}[./]\d{4})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?\s*(.*)$")
_PDF_AMOUNTS_END = re.compile(
    r"(-?[\d.]+,\d{2})\s*(?:TL|TRY)?\s+(-?[\d.]+,\d{2})\s*(?:TL|TRY)?\s*$"
)


def _parse_pdf(content: bytes) -> ParsedStatement:
    text = extract_pdf_text(content).text
    if not text.strip():
        raise StatementParseError(
            "PDF'te metin yok (taranmış görüntü olabilir); OCR gerekir."
        )
    iban_match = IBAN_RE.search(text)
    statement = ParsedStatement(
        source_format="pdf",
        iban=normalize_iban(iban_match.group(0)) if iban_match else None,
        bank_name=detect_bank(text[:3000]),
        lines=[],
    )
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        start = _PDF_TX_START.match(line)
        if start and parse_date(start.group(1)):
            current = {"date": parse_date(start.group(1)), "time": start.group(2), "parts": []}
            line = start.group(3)
        if current is None:
            continue
        end = _PDF_AMOUNTS_END.search(line)
        if end:
            current["parts"].append(line[: end.start()].strip())
            amount, balance = parse_amount(end.group(1)), parse_amount(end.group(2))
            description = re.sub(r"\s+", " ", " ".join(p for p in current["parts"] if p)).strip()
            if amount:
                statement.lines.append(
                    StatementLine(
                        transaction_date=current["date"],
                        description=description[:500] or "Banka hareketi",
                        amount=amount,
                        balance=balance,
                        time=current["time"],
                    )
                )
            current = None
        else:
            current["parts"].append(line)
    if not statement.lines:
        raise StatementParseError("PDF ekstresinde hareket satırı tanınamadı.")
    return statement


# --- Doğrulama ve kimlik ----------------------------------------------------


def _chronological(lines: list[StatementLine]) -> list[StatementLine]:
    """Bakiye zincirine en iyi uyan sırayı seçer (ekstreler eskiden-yeniye veya tersi)."""

    def score(seq: list[StatementLine]) -> int:
        return sum(
            1
            for prev, cur in zip(seq, seq[1:])
            if prev.balance is not None and cur.balance is not None
            and prev.balance + cur.amount == cur.balance
        )

    forward, backward = list(lines), list(reversed(lines))
    return backward if score(backward) > score(forward) else forward


def _check_balances(statement: ParsedStatement) -> None:
    ordered = _chronological(statement.lines)
    statement.lines = ordered
    for prev, cur in zip(ordered, ordered[1:]):
        if prev.balance is None or cur.balance is None:
            continue
        statement.balance_checked += 1
        if prev.balance + cur.amount != cur.balance:
            statement.balance_mismatches.append(
                f"{cur.transaction_date.isoformat()} {cur.description[:40]}: "
                f"{prev.balance} + {cur.amount} = {prev.balance + cur.amount}, ekstrede {cur.balance}"
            )


def _assign_ids(statement: ParsedStatement) -> None:
    """Yeniden yüklemede aynı kalan, içerikten türetilmiş hareket kimliği (mükerrer önleme)."""
    seen: dict[str, int] = {}
    for line in statement.lines:
        basis = "|".join(
            str(x) for x in (line.transaction_date, line.time, line.amount, line.balance,
                             line.reference, line.description)
        )
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]
        seen[digest] = seen.get(digest, 0) + 1
        line.external_id = digest if seen[digest] == 1 else f"{digest}-{seen[digest]}"


def parse_statement(content: bytes, filename: str = "") -> ParsedStatement:
    if not content:
        raise StatementParseError("Dosya boş.")
    name = filename.lower()
    if content[:4] == b"%PDF":
        statement = _parse_pdf(content)
    elif content[:2] == b"PK":
        statement = _parse_table(_rows_from_xlsx(content), "xlsx")
    elif content[:3] in (b"\xff\xd8\xff",) or content[:8] == b"\x89PNG\r\n\x1a\n":
        raise StatementParseError(
            "Ekstre fotoğrafı okunamıyor: bu sunucuda OCR motoru kurulu değil. "
            "Bankadan Excel/CSV/PDF ekstre indirip yükleyin."
        )
    elif name.endswith(".xls") or content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise StatementParseError(
            "Eski .xls biçimi desteklenmiyor; dosyayı Excel'de .xlsx olarak kaydedip yükleyin."
        )
    else:
        statement = _parse_table(_rows_from_csv(content), "csv")
    if not statement.lines:
        raise StatementParseError("Ekstrede içe aktarılacak hareket bulunamadı.")
    statement.bank_name = statement.bank_name or detect_bank(filename)
    _check_balances(statement)
    _assign_ids(statement)
    if statement.balance_mismatches:
        statement.warnings.append(
            f"{len(statement.balance_mismatches)} satırda bakiye zinciri tutmuyor; kontrol edin."
        )
    return statement
