import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation


class ZReportParseError(ValueError):
    pass


@dataclass(frozen=True)
class VATBreakdown:
    rate: Decimal
    base: Decimal | None
    tax: Decimal | None


@dataclass(frozen=True)
class ZReportData:
    report_date: str | None
    report_number: str | None
    fiscal_id: str | None
    total: Decimal | None
    cash_total: Decimal | None
    card_total: Decimal | None
    vat_breakdown: tuple[VATBreakdown, ...]
    review_status: str
    review_reasons: tuple[str, ...]


AMOUNT = r"([-+]?\s*[\d.]+(?:,\d{1,2})?)"


def _amount(value: str) -> Decimal:
    try:
        return Decimal(value.replace(" ", "").replace(".", "").replace(",", ".")).quantize(
            Decimal("0.01")
        )
    except InvalidOperation as exc:
        raise ZReportParseError(f"Geçersiz Z raporu tutarı: {value}") from exc


def _find_amount(text: str, labels: tuple[str, ...]) -> Decimal | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\s*[:=-]?\s*{AMOUNT}", text, re.IGNORECASE)
    return _amount(match.group(1)) if match else None


def parse_z_report(text: str) -> ZReportData:
    if not text.strip():
        raise ZReportParseError("Z raporu metni boş olamaz.")
    report_date_match = re.search(r"\b(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})\b", text)
    number_match = re.search(r"\bZ\s*(?:NO|NUMARASI)\s*[:#-]?\s*([A-Z0-9-]+)", text, re.IGNORECASE)
    fiscal_match = re.search(
        r"(?:(?:ÖKC\s+)?SİCİL\s+NO|ÖKC)\s*[:#-]?\s*([A-Z0-9-]+)",
        text,
        re.IGNORECASE,
    )
    total = _find_amount(text, ("TOPLAM", "GENEL TOPLAM"))
    cash = _find_amount(text, ("NAKİT", "NAKIT"))
    card = _find_amount(text, ("KREDİ KARTI", "KREDI KARTI", "KART"))
    reasons: list[str] = []
    if report_date_match is None:
        reasons.append("Tarih bulunamadı")
    if total is None:
        reasons.append("Toplam bulunamadı")
    if cash is None and card is None:
        reasons.append("Ödeme dağılımı bulunamadı")
    if total is not None and cash is not None and card is not None:
        if cash + card != total:
            reasons.append("Nakit ve kart toplamı genel toplamla eşleşmiyor")
    return ZReportData(
        report_date=report_date_match.group(1) if report_date_match else None,
        report_number=number_match.group(1) if number_match else None,
        fiscal_id=fiscal_match.group(1) if fiscal_match else None,
        total=total,
        cash_total=cash,
        card_total=card,
        vat_breakdown=(),
        review_status="needs_review" if reasons else "ready",
        review_reasons=tuple(reasons),
    )


def z_report_metadata(report: ZReportData) -> dict[str, object]:
    result = asdict(report)
    result["vat_breakdown"] = [asdict(item) for item in report.vat_breakdown]
    for key in ("total", "cash_total", "card_total"):
        if result[key] is not None:
            result[key] = str(result[key])
    return result
