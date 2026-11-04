import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation


class ExpenseSlipParseError(ValueError):
    pass


@dataclass(frozen=True)
class ExpenseSlipData:
    document_date: str | None
    seller_name: str | None
    seller_tax_id: str | None
    description: str | None
    base_amount: Decimal | None
    vat_amount: Decimal | None
    total_amount: Decimal | None
    review_status: str
    review_reasons: tuple[str, ...]


def _amount(value: str) -> Decimal:
    try:
        return Decimal(value.replace(" ", "").replace(".", "").replace(",", ".")).quantize(
            Decimal("0.01")
        )
    except InvalidOperation as exc:
        raise ExpenseSlipParseError(f"Geçersiz gider pusulası tutarı: {value}") from exc


FIELD_LABELS = (
    "SATAN",
    "SATICI",
    "BELGE SAHİBİ",
    "BELGE SAHIBI",
    "VKN",
    "TCKN",
    "VERGİ KİMLİK NO",
    "VERGI KIMLIK NO",
    "AÇIKLAMA",
    "ACIKLAMA",
    "MAL/HİZMET",
    "MAL/HIZMET",
    "MATRAH",
    "BRÜT TUTAR",
    "BRUT TUTAR",
    "KDV",
    "KDV TUTARI",
    "TOPLAM",
    "ÖDENECEK",
    "ODENECEK",
)


def _find(text: str, labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    stop_pattern = "|".join(re.escape(label) for label in FIELD_LABELS)
    match = re.search(
        rf"(?:{label_pattern})\s*[:=-]?\s*(.+?)(?=\s+(?:{stop_pattern})\s*[:=-]|\s*$)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _find_amount(text: str, labels: tuple[str, ...]) -> Decimal | None:
    value = _find(text, labels)
    return _amount(value) if value else None


def parse_expense_slip(text: str) -> ExpenseSlipData:
    if not text.strip():
        raise ExpenseSlipParseError("Gider pusulası metni boş olamaz.")
    date_match = re.search(r"\b(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})\b", text)
    tax_match = re.search(
        r"(?:VKN|TCKN|VERGİ KİMLİK NO|VERGI KIMLIK NO)\s*[:=-]?\s*(\d{10,11})",
        text,
        re.IGNORECASE,
    )
    name = _find(text, ("SATAN", "SATICI", "BELGE SAHİBİ", "BELGE SAHIBI"))
    description = _find(text, ("AÇIKLAMA", "ACIKLAMA", "MAL/HİZMET", "MAL/HIZMET"))
    base = _find_amount(text, ("MATRAH", "BRÜT TUTAR", "BRUT TUTAR"))
    vat = _find_amount(text, ("KDV", "KDV TUTARI"))
    total = _find_amount(text, ("TOPLAM", "ÖDENECEK", "ODENECEK"))
    reasons: list[str] = []
    if date_match is None:
        reasons.append("Tarih bulunamadı")
    if name is None:
        reasons.append("Satıcı bulunamadı")
    if base is None:
        reasons.append("Matrah bulunamadı")
    if total is None:
        reasons.append("Toplam bulunamadı")
    if base is not None and vat is not None and total is not None and base + vat != total:
        reasons.append("Matrah ve KDV toplamla eşleşmiyor")
    return ExpenseSlipData(
        document_date=date_match.group(1) if date_match else None,
        seller_name=name,
        seller_tax_id=tax_match.group(1) if tax_match else None,
        description=description,
        base_amount=base,
        vat_amount=vat,
        total_amount=total,
        review_status="needs_review" if reasons else "ready",
        review_reasons=tuple(reasons),
    )


def expense_slip_metadata(data: ExpenseSlipData) -> dict[str, object]:
    result = asdict(data)
    for key in ("base_amount", "vat_amount", "total_amount"):
        if result[key] is not None:
            result[key] = str(result[key])
    return result
