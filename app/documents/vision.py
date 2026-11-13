"""Yerel görsel dil modeliyle (Ollama) fiş / Z raporu okuma.

Veri makineden çıkmaz: Ollama adresi varsayılan olarak yalnızca yerel ağ
arayüzüne (localhost) izin verir. Küçük yerel modeller rakam karıştırabileceği
için model çıktısı asla doğrudan güvenilmez; her alan deterministik kontrollerden
(KDV aritmetiği, ödeme toplamı, VKN/TCKN sağlaması, Z kümülatif sürekliliği)
geçer ve tutarsız belge `needs_review` olarak işaretlenir.
"""

import base64
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

from app.core.config import get_settings

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_CENT = Decimal("0.01")
# Termal fişlerde KDV yuvarlaması kuruş farkı yaratabilir.
_TOLERANCE = Decimal("0.05")
# Z raporunda KDV fiş fiş hesaplanıp toplandığı için yuvarlama farkı birikir.
_Z_VAT_TOLERANCE = Decimal("1.00")

DOCUMENT_TYPES = ("z_raporu", "perakende_fis", "pos_slip", "diger")


class VisionError(RuntimeError):
    pass


class VisionUnavailableError(VisionError):
    """Ollama çalışmıyor veya model yüklü değil."""


# --- Model çıktısı şeması ---------------------------------------------------

_AMOUNT = {"type": ["number", "null"]}
_TEXT = {"type": ["string", "null"]}

RECEIPT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "belgeler": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tur": {"type": "string", "enum": list(DOCUMENT_TYPES)},
                    "isyeri_unvan": _TEXT,
                    "vergi_dairesi": _TEXT,
                    "vkn_tckn": _TEXT,
                    "tarih": _TEXT,
                    "saat": _TEXT,
                    "fis_no": _TEXT,
                    "z_no": _TEXT,
                    "toplam": _AMOUNT,
                    "toplam_kdv": _AMOUNT,
                    "kdv_dagilimi": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "oran": {"type": "number"},
                                "tutar": _AMOUNT,
                                "kdv": _AMOUNT,
                            },
                            "required": ["oran", "tutar", "kdv"],
                        },
                    },
                    "nakit": _AMOUNT,
                    "kredi_karti": _AMOUNT,
                    "kumulatif_toplam": _AMOUNT,
                    "kumulatif_kdv": _AMOUNT,
                },
                "required": ["tur", "tarih", "toplam", "toplam_kdv"],
            },
        }
    },
    "required": ["belgeler"],
}

PROMPT = """Bu fotoğraf Türkiye'de düzenlenmiş bir veya birden fazla yazar kasa fişi,
Z raporu veya POS slibi içerir. Fotoğraf yan dönmüş olabilir. Fotoğraftaki HER
belgeyi ayrı bir kayıt olarak çıkar.

Kurallar:
- Sadece belgede açıkça yazanı yaz. Okuyamadığın alanı null bırak, ASLA tahmin etme.
- Tutarlar sayı olsun: "*1.270,00" -> 1270.00 ; "*115,45" -> 115.45 (baştaki * işaretini at).
- tarih GG.AA.YYYY biçiminde.
- tur: Z RAPORU yazıyorsa "z_raporu"; satış fişi ise "perakende_fis"; yalnızca banka
  POS slibi ise "pos_slip"; diğer durumlarda "diger".
- toplam = TOPLAM satırı (KDV dahil). toplam_kdv = TOPKDV / KDV satırı.
- kdv_dagilimi: her KDV oranı için o orandaki KDV dahil tutar ve KDV.
- Z raporunda nakit = NAKİT, kredi_karti = K.KARTI / KREDİ KARTI tutarı,
  kumulatif_toplam = KÜM. TOPLAM, kumulatif_kdv = KÜM. TOPKDV.
- vkn_tckn: "V.D." / "VD" yanındaki 10 veya 11 haneli numara.
Yalnızca JSON döndür."""


# --- Veri yapıları ----------------------------------------------------------


@dataclass
class VatLine:
    rate: Decimal
    gross: Decimal | None
    tax: Decimal | None


@dataclass
class ReceiptRead:
    document_type: str
    seller_name: str | None
    tax_office: str | None
    tax_id: str | None
    document_date: str | None  # ISO
    time: str | None
    receipt_no: str | None
    z_no: str | None
    total: Decimal | None
    total_vat: Decimal | None
    vat_lines: list[VatLine] = field(default_factory=list)
    cash: Decimal | None = None
    card: Decimal | None = None
    cumulative_total: Decimal | None = None
    cumulative_vat: Decimal | None = None
    review_status: str = "ready"
    review_reasons: list[str] = field(default_factory=list)

    def flag(self, reason: str) -> None:
        self.review_status = "needs_review"
        if reason not in self.review_reasons:
            self.review_reasons.append(reason)


@dataclass
class VisionResult:
    model: str
    documents: list[ReceiptRead]
    raw: dict


def receipt_metadata(result: VisionResult) -> dict:
    def conv(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, list):
            return [conv(v) for v in value]
        if isinstance(value, dict):
            return {k: conv(v) for k, v in value.items()}
        return value

    return {
        "model": result.model,
        "documents": [conv(asdict(doc)) for doc in result.documents],
    }


# --- Normalizasyon ----------------------------------------------------------


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return Decimal(str(value)).quantize(_CENT)
        text = str(value).replace("*", "").replace("₺", "").replace("TL", "").strip()
        text = text.replace(" ", "")
        if "," in text:  # Türkçe biçim: 1.270,00
            text = text.replace(".", "").replace(",", ".")
        return Decimal(text).quantize(_CENT)
    except (InvalidOperation, ValueError):
        return None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _iso_date(value: object) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def is_valid_vkn(value: str) -> bool:
    if not re.fullmatch(r"\d{10}", value):
        return False
    digits = [int(c) for c in value]
    total = 0
    for i in range(9):
        tmp = (digits[i] + 9 - i) % 10
        v = (tmp * 2 ** (9 - i)) % 9
        if tmp != 0 and v == 0:
            v = 9
        total += v
    return (10 - total % 10) % 10 == digits[9]


def is_valid_tckn(value: str) -> bool:
    if not re.fullmatch(r"[1-9]\d{10}", value):
        return False
    d = [int(c) for c in value]
    tenth = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    return tenth == d[9] and sum(d[:10]) % 10 == d[10]


def normalize_document(item: dict) -> ReceiptRead:
    doc_type = item.get("tur") if item.get("tur") in DOCUMENT_TYPES else "diger"
    tax_id = re.sub(r"\D", "", _text(item.get("vkn_tckn")) or "") or None
    vat_lines = []
    for line in item.get("kdv_dagilimi") or []:
        rate = _decimal(line.get("oran"))
        if rate is None:
            continue
        vat_lines.append(VatLine(rate=rate, gross=_decimal(line.get("tutar")),
                                 tax=_decimal(line.get("kdv"))))
    return ReceiptRead(
        document_type=doc_type,
        seller_name=_text(item.get("isyeri_unvan")),
        tax_office=_text(item.get("vergi_dairesi")),
        tax_id=tax_id,
        document_date=_iso_date(item.get("tarih")),
        time=_text(item.get("saat")),
        receipt_no=_text(item.get("fis_no")),
        z_no=_text(item.get("z_no")),
        total=_decimal(item.get("toplam")),
        total_vat=_decimal(item.get("toplam_kdv")),
        vat_lines=vat_lines,
        cash=_decimal(item.get("nakit")),
        card=_decimal(item.get("kredi_karti")),
        cumulative_total=_decimal(item.get("kumulatif_toplam")),
        cumulative_vat=_decimal(item.get("kumulatif_kdv")),
    )


# --- Deterministik doğrulama -------------------------------------------------


def _close(a: Decimal, b: Decimal, tolerance: Decimal = _TOLERANCE) -> bool:
    return abs(a - b) <= tolerance


def _vat_from_gross(gross: Decimal, rate: Decimal) -> Decimal:
    return (gross * rate / (Decimal(100) + rate)).quantize(_CENT)


def validate_document(doc: ReceiptRead) -> ReceiptRead:
    vat_tolerance = _Z_VAT_TOLERANCE if doc.document_type == "z_raporu" else _TOLERANCE
    if doc.document_type == "diger":
        doc.flag("Belge türü tanınamadı.")
    if doc.document_date is None:
        doc.flag("Tarih okunamadı veya geçersiz.")
    if doc.total is None or doc.total <= 0:
        doc.flag("Toplam tutar okunamadı.")
    if doc.document_type == "pos_slip":
        # POS slibi tek başına mali belge değildir; fişle eşleştirilmelidir.
        doc.flag("Yalnızca POS slibi: mali fişle eşleştirilmeli.")
        return doc

    if doc.tax_id:
        if not (is_valid_vkn(doc.tax_id) or is_valid_tckn(doc.tax_id)):
            doc.flag(f"VKN/TCKN sağlaması tutmuyor: {doc.tax_id}")
    elif doc.document_type == "perakende_fis":
        doc.flag("Satıcı VKN/TCKN okunamadı.")

    if doc.total is not None and doc.total_vat is not None:
        if doc.vat_lines:
            for line in doc.vat_lines:
                if line.gross is not None and line.tax is not None and not _close(
                    _vat_from_gross(line.gross, line.rate), line.tax, vat_tolerance
                ):
                    doc.flag(
                        f"%{line.rate} KDV tutarı tutarsız: {line.gross} için "
                        f"{_vat_from_gross(line.gross, line.rate)} beklenirdi, {line.tax} okundu."
                    )
            gross_sum = sum((l.gross for l in doc.vat_lines if l.gross is not None), Decimal(0))
            tax_sum = sum((l.tax for l in doc.vat_lines if l.tax is not None), Decimal(0))
            if gross_sum and not _close(gross_sum, doc.total, Decimal("1.00")):
                doc.flag(f"KDV dağılımı toplamı ({gross_sum}) genel toplamla ({doc.total}) uyuşmuyor.")
            if tax_sum and not _close(tax_sum, doc.total_vat, vat_tolerance):
                doc.flag(f"KDV dağılımı ({tax_sum}) TOPKDV ile ({doc.total_vat}) uyuşmuyor.")
        else:
            plausible = any(
                _close(_vat_from_gross(doc.total, Decimal(rate)), doc.total_vat, vat_tolerance)
                for rate in (1, 10, 20)
            )
            if not plausible:
                doc.flag("TOPKDV hiçbir standart KDV oranıyla (%1/%10/%20) uyuşmuyor.")
    elif doc.total is not None:
        doc.flag("TOPKDV okunamadı.")

    if doc.document_type == "z_raporu":
        if doc.z_no is None:
            doc.flag("Z numarası okunamadı.")
        if doc.total is not None and (doc.cash is not None or doc.card is not None):
            paid = (doc.cash or Decimal(0)) + (doc.card or Decimal(0))
            if not _close(paid, doc.total, Decimal("0.01")):
                doc.flag(f"Nakit + kart ({paid}) Z toplamıyla ({doc.total}) uyuşmuyor.")
    return doc


def _z_number(doc: ReceiptRead) -> int | None:
    digits = re.sub(r"\D", "", doc.z_no or "")
    return int(digits) if digits else None


def validate_z_sequence(reports: list[ReceiptRead]) -> None:
    """Ardışık Z raporlarında: önceki KÜM. TOPLAM + bugünkü TOPLAM = bugünkü KÜM. TOPLAM.

    Kümülatif zincir tek bir rakam okuma hatasını bile yakalar.
    """
    numbered = sorted(
        (r for r in reports if r.document_type == "z_raporu" and _z_number(r) is not None),
        key=_z_number,
    )
    for prev, cur in zip(numbered, numbered[1:]):
        if _z_number(cur) != _z_number(prev) + 1:
            continue
        pairs = [
            ("KÜM. TOPLAM", prev.cumulative_total, cur.total, cur.cumulative_total),
            ("KÜM. TOPKDV", prev.cumulative_vat, cur.total_vat, cur.cumulative_vat),
        ]
        for label, prev_cum, today, cur_cum in pairs:
            if None in (prev_cum, today, cur_cum):
                continue
            if not _close(prev_cum + today, cur_cum, Decimal("0.01")):
                reason = (
                    f"{label} zinciri kopuk: Z {_z_number(prev)} ({prev_cum}) + "
                    f"Z {_z_number(cur)} ({today}) = {prev_cum + today}, okunan {cur_cum}."
                )
                prev.flag(reason)
                cur.flag(reason)


# --- Ollama istemcisi -------------------------------------------------------


def _check_local(url: str) -> None:
    settings = get_settings()
    host = urlparse(url).hostname or ""
    if host not in _LOCAL_HOSTS and not settings.vision_allow_remote:
        raise VisionError(
            "Görsel model adresi yerel değil. Mükellef verisi makineden çıkmamalıdır "
            "(VISION_ALLOW_REMOTE ile bilinçli olarak açılabilir)."
        )


def prepare_image(content: bytes, max_side: int) -> bytes:
    """EXIF yönünü uygular ve uzun kenarı sınırlar (küçük GPU'da hız ve bellek için)."""
    with Image.open(io.BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((max_side, max_side))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=90)
        return out.getvalue()


def read_receipt_image(
    content: bytes,
    *,
    client: httpx.Client | None = None,
) -> VisionResult:
    settings = get_settings()
    url = settings.vision_ollama_url.rstrip("/")
    _check_local(url)
    payload = {
        "model": settings.vision_model,
        "prompt": PROMPT,
        "images": [base64.b64encode(prepare_image(content, settings.vision_max_side)).decode()],
        "format": RECEIPT_SCHEMA,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=settings.vision_timeout_seconds)
    try:
        response = client.post(f"{url}/api/generate", json=payload)
    except httpx.HTTPError as exc:
        raise VisionUnavailableError(
            f"Ollama'ya ulaşılamadı ({url}). Ollama çalışıyor mu?"
        ) from exc
    finally:
        if owns_client:
            client.close()
    if response.status_code == 404:
        raise VisionUnavailableError(
            f"'{settings.vision_model}' modeli yüklü değil: ollama pull {settings.vision_model}"
        )
    if response.status_code >= 400:
        raise VisionError(f"Ollama hatası {response.status_code}: {response.text[:200]}")
    try:
        raw = json.loads(response.json()["response"])
    except (KeyError, ValueError, TypeError) as exc:
        raise VisionError("Model geçerli JSON döndürmedi.") from exc

    documents = [validate_document(normalize_document(item)) for item in raw.get("belgeler", [])]
    validate_z_sequence(documents)
    if not documents:
        raise VisionError("Görselde okunabilir belge bulunamadı.")
    return VisionResult(model=settings.vision_model, documents=documents, raw=raw)
