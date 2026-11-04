import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation


class SMMParseError(ValueError):
    pass


@dataclass(frozen=True)
class SMMData:
    document_date: str | None
    receipt_number: str | None
    professional_name: str | None
    professional_tax_id: str | None
    client_name: str | None
    gross_amount: Decimal | None
    withholding_amount: Decimal | None
    vat_amount: Decimal | None
    net_amount: Decimal | None
    review_status: str
    review_reasons: tuple[str, ...]


def _amount(value: str) -> Decimal:
    try:
        return Decimal(value.replace(" ", "").replace(".", "").replace(",", ".")).quantize(
            Decimal("0.01")
        )
    except InvalidOperation as exc:
        raise SMMParseError(f"Geçersiz SMM tutarı: {value}") from exc


def _find_amount(text: str, labels: tuple[str, ...]) -> Decimal | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{label_pattern})\s*[:=-]?\s*([-+]?\s*[\d.]+(?:,\d{{1,2}})?)", text, re.I)
    return _amount(match.group(1)) if match else None


FIELD_LABELS = (
    "SERBEST MESLEK ERBABI",
    "DÜZENLEYEN",
    "DUZENLEYEN",
    "MÜŞTERİ",
    "MUSTERI",
    "ALICI",
    "VKN",
    "TCKN",
    "BRÜT",
    "BRUT",
    "BRÜT TUTAR",
    "BRUT TUTAR",
    "STOPAJ",
    "GELİR VERGİSİ KESİNTİSİ",
    "KDV",
    "KDV TUTARI",
    "NET",
    "ÖDENECEK",
    "ODENECEK",
)


def _find_text(text: str, labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    stop_pattern = "|".join(re.escape(label) for label in FIELD_LABELS)
    match = re.search(
        rf"(?:{label_pattern})\s*[:=-]?\s*(.+?)(?=\s+(?:{stop_pattern})\s*[:=-]|\s*$)",
        text,
        re.I,
    )
    return match.group(1).strip() if match else None


def parse_smm(text: str) -> SMMData:
    if not text.strip():
        raise SMMParseError("SMM metni boş olamaz.")
    date_match = re.search(r"\b(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})\b", text)
    number_match = re.search(r"(?:MAKBUZ|SMM)\s*(?:NO|NUMARA)?\s*[:#-]?\s*([A-Z0-9-]+)", text, re.I)
    tax_match = re.search(r"(?:VKN|TCKN)\s*[:=-]?\s*(\d{10,11})", text, re.I)
    gross = _find_amount(text, ("BRÜT", "BRUT", "BRÜT TUTAR", "BRUT TUTAR"))
    withholding = _find_amount(text, ("STOPAJ", "GELİR VERGİSİ KESİNTİSİ"))
    vat = _find_amount(text, ("KDV", "KDV TUTARI"))
    net = _find_amount(text, ("NET", "ÖDENECEK", "ODENECEK"))
    reasons: list[str] = []
    if date_match is None:
        reasons.append("Tarih bulunamadı")
    if gross is None:
        reasons.append("Brüt tutar bulunamadı")
    if net is None:
        reasons.append("Net/ödenecek tutar bulunamadı")
    if gross is not None and withholding is not None and net is not None:
        expected_net = gross - withholding + (vat or Decimal("0.00"))
        if expected_net != net:
            reasons.append("Brüt, KDV ve stopaj net tutarla eşleşmiyor")
    return SMMData(
        document_date=date_match.group(1) if date_match else None,
        receipt_number=number_match.group(1) if number_match else None,
        professional_name=_find_text(text, ("SERBEST MESLEK ERBABI", "DÜZENLEYEN", "DUZENLEYEN")),
        professional_tax_id=tax_match.group(1) if tax_match else None,
        client_name=_find_text(text, ("MÜŞTERİ", "MUSTERI", "ALICI")),
        gross_amount=gross,
        withholding_amount=withholding,
        vat_amount=vat,
        net_amount=net,
        review_status="needs_review" if reasons else "ready",
        review_reasons=tuple(reasons),
    )


def smm_metadata(data: SMMData) -> dict[str, object]:
    result = asdict(data)
    for key in ("gross_amount", "withholding_amount", "vat_amount", "net_amount"):
        if result[key] is not None:
            result[key] = str(result[key])
    return result
