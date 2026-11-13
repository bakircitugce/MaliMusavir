"""Yerel görsel model okuyucusu: Ollama sahte transport ile, deterministik kontroller gerçek
fiş/Z raporu yapısını taklit eden sentetik verilerle test edilir."""

import io
import json

import httpx
import pytest
from PIL import Image

from app.core.config import get_settings
from app.documents.vision import (
    VisionError,
    VisionUnavailableError,
    is_valid_tckn,
    is_valid_vkn,
    normalize_document,
    read_receipt_image,
    validate_document,
    validate_z_sequence,
)


def _vkn(prefix: str) -> str:
    """9 haneli önekten geçerli bir VKN üretir (sağlama hanesi hesaplanarak)."""
    digits = [int(c) for c in prefix]
    total = 0
    for i in range(9):
        tmp = (digits[i] + 9 - i) % 10
        v = (tmp * 2 ** (9 - i)) % 9
        if tmp != 0 and v == 0:
            v = 9
        total += v
    return prefix + str((10 - total % 10) % 10)


VALID_VKN = _vkn("123456789")


def _image() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 80), "white").save(out, format="JPEG")
    return out.getvalue()


def _client(payload: dict | None = None, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["options"]["temperature"] == 0
        assert body["images"]
        return httpx.Response(status, json={"response": json.dumps(payload or {})})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _fis(**overrides) -> dict:
    item = {
        "tur": "perakende_fis",
        "isyeri_unvan": "ÖRNEK LOKANTA",
        "vkn_tckn": VALID_VKN,
        "tarih": "04/09/2026",
        "fis_no": "0003",
        "toplam": 1270.0,
        "toplam_kdv": 115.45,
        "kdv_dagilimi": [{"oran": 10, "tutar": 1270.0, "kdv": 115.45}],
    }
    item.update(overrides)
    return item


def _z(z_no: str, total: float, vat: float, cum: float, cum_vat: float, **kw) -> dict:
    item = {
        "tur": "z_raporu",
        "tarih": "01-09-2026",
        "z_no": z_no,
        "toplam": total,
        "toplam_kdv": vat,
        "kdv_dagilimi": [{"oran": 10, "tutar": total, "kdv": vat}],
        "nakit": 0,
        "kredi_karti": total,
        "kumulatif_toplam": cum,
        "kumulatif_kdv": cum_vat,
    }
    item.update(kw)
    return item


# --- Sağlama algoritmaları --------------------------------------------------


def test_vkn_and_tckn_checksums() -> None:
    assert is_valid_vkn(VALID_VKN)
    assert not is_valid_vkn(VALID_VKN[:-1] + str((int(VALID_VKN[-1]) + 1) % 10))
    assert not is_valid_vkn("12345")
    assert is_valid_tckn("10000000146")
    assert not is_valid_tckn("10000000147")
    assert not is_valid_tckn("01234567890")


# --- Normalizasyon ve tekil belge kontrolleri -------------------------------


def test_normalizes_turkish_amounts_and_dates() -> None:
    doc = normalize_document(_fis(toplam="*1.270,00", toplam_kdv="*115,45", tarih="12/09/26"))
    assert str(doc.total) == "1270.00"
    assert str(doc.total_vat) == "115.45"
    assert doc.document_date == "2026-09-12"


def test_consistent_receipt_is_ready() -> None:
    doc = validate_document(normalize_document(_fis()))
    assert doc.review_status == "ready", doc.review_reasons


def test_misread_vat_digit_is_flagged() -> None:
    doc = validate_document(
        normalize_document(_fis(toplam_kdv=175.45, kdv_dagilimi=[{"oran": 10, "tutar": 1270, "kdv": 175.45}]))
    )
    assert doc.review_status == "needs_review"
    assert any("KDV" in r for r in doc.review_reasons)


def test_mixed_rate_receipt() -> None:
    doc = validate_document(
        normalize_document(
            _fis(
                toplam=555.0,
                toplam_kdv=56.14,
                kdv_dagilimi=[
                    {"oran": 20, "tutar": 75.0, "kdv": 12.50},
                    {"oran": 10, "tutar": 480.0, "kdv": 43.64},
                ],
            )
        )
    )
    assert doc.review_status == "ready", doc.review_reasons


def test_invalid_tax_id_and_missing_fields_are_flagged() -> None:
    wrong = VALID_VKN[:-1] + str((int(VALID_VKN[-1]) + 1) % 10)
    doc = validate_document(normalize_document(_fis(vkn_tckn=wrong, tarih=None)))
    reasons = " ".join(doc.review_reasons)
    assert doc.review_status == "needs_review"
    assert "sağlaması" in reasons
    assert "Tarih" in reasons


def test_vat_without_breakdown_must_match_a_standard_rate() -> None:
    ok = validate_document(normalize_document(_fis(kdv_dagilimi=[])))
    bad = validate_document(normalize_document(_fis(kdv_dagilimi=[], toplam_kdv=99.0)))
    assert ok.review_status == "ready"
    assert bad.review_status == "needs_review"


def test_pos_slip_alone_needs_matching() -> None:
    doc = validate_document(normalize_document({"tur": "pos_slip", "tarih": "05.06.2026",
                                                "toplam": 185, "toplam_kdv": None}))
    assert doc.review_status == "needs_review"


# --- Z raporu kontrolleri ---------------------------------------------------


def test_z_report_payments_must_sum_to_total() -> None:
    ok = validate_document(normalize_document(
        _z("3.671", 8185, 744.14, 5686331.60, 481376.05, nakit=50, kredi_karti=8135)
    ))
    bad = validate_document(normalize_document(
        _z("3.671", 8185, 744.14, 5686331.60, 481376.05, nakit=50, kredi_karti=8155)
    ))
    assert ok.review_status == "ready", ok.review_reasons
    assert bad.review_status == "needs_review"


def test_z_cumulative_chain_detects_single_digit_error() -> None:
    chain = [
        _z("3.671", 8185, 744.14, 5686331.60, 481376.05, nakit=50, kredi_karti=8135),
        _z("3.672", 13920, 1265.46, 5700251.60, 482641.51),
        _z("3.673", 14130, 1284.54, 5714381.60, 483926.05),
    ]
    docs = [validate_document(normalize_document(item)) for item in chain]
    validate_z_sequence(docs)
    assert all(d.review_status == "ready" for d in docs), [d.review_reasons for d in docs]

    chain[2]["kumulatif_toplam"] = 5714881.60  # tek rakam hatası
    docs = [validate_document(normalize_document(item)) for item in chain]
    validate_z_sequence(docs)
    assert docs[0].review_status == "ready"
    assert docs[1].review_status == docs[2].review_status == "needs_review"
    assert "zinciri" in docs[2].review_reasons[0]


# --- Ollama istemcisi -------------------------------------------------------


def test_read_receipt_image_with_multiple_documents() -> None:
    result = read_receipt_image(
        _image(), client=_client({"belgeler": [_fis(), _fis(fis_no="0004", toplam_kdv=10.0)]})
    )
    assert result.model == get_settings().vision_model
    assert [d.review_status for d in result.documents] == ["ready", "needs_review"]


def test_missing_model_and_unreachable_server() -> None:
    with pytest.raises(VisionUnavailableError):
        read_receipt_image(_image(), client=_client(status=404))

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kapalı")

    with pytest.raises(VisionUnavailableError):
        read_receipt_image(_image(), client=httpx.Client(transport=httpx.MockTransport(boom)))


def test_empty_result_is_an_error() -> None:
    with pytest.raises(VisionError):
        read_receipt_image(_image(), client=_client({"belgeler": []}))


def test_remote_ollama_is_refused_by_default(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "vision_ollama_url", "https://bulut.example.com")
    with pytest.raises(VisionError, match="yerel değil"):
        read_receipt_image(_image(), client=_client({"belgeler": [_fis()]}))


# --- Belge hattı entegrasyonu -----------------------------------------------


async def test_image_document_is_read_by_local_model(db_factory, monkeypatch) -> None:
    from uuid import uuid4

    from app.core.models import Belge
    from app.documents import extractor

    class MemoryStorage:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}

        async def put(self, key: str, data: bytes) -> None:
            self.files[key] = data

        async def get(self, key: str) -> bytes:
            return self.files[key]

        async def delete(self, key: str) -> None:
            self.files.pop(key, None)

    chain = {"belgeler": [
        _z("3.672", 13920, 1265.46, 5700251.60, 482641.51),
        _z("3.673", 14130, 1284.54, 5714381.60, 483926.05),
    ]}
    # Gerçek Ollama çağrılmaz: sahte transport kullanılır.
    monkeypatch.setattr(get_settings(), "vision_backend", "ollama")
    monkeypatch.setattr(
        extractor, "read_receipt_image",
        lambda content: read_receipt_image(content, client=_client(chain)),
    )
    storage = MemoryStorage()
    await storage.put("k/z.jpg", _image())
    async with db_factory() as session:
        belge = Belge(
            buro_id=uuid4(), mukellef_id=uuid4(), original_filename="z.jpg",
            content_type="image/jpeg", size_bytes=10, sha256="c" * 64,
            storage_key="k/z.jpg", document_type="image",
        )
        session.add(belge)
        await session.commit()
        result = await extractor.extract_document(session, storage, belge)

    assert result.extraction_status == "completed"
    assert result.document_category == "z_report"
    assert result.classification_status == "classified", result.classification_reasons
    meta = result.extraction_metadata
    assert meta["extractor"].startswith("ollama:")
    assert len(meta["receipts"]["documents"]) == 2
