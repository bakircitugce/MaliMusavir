"""pypdf 6.x yükseltme regresyon testleri.

Kapsam: strict=True ile geçerli PDF okuma, çok sayfa, metin çıkarımı, geçersiz/boş içerik ve
pypdf 6.x'te eklenen DoS sınırlarının (FlateDecode/akış/görüntü boyutu) uygulamada çökme değil
güvenli `PDFExtractionError`'a dönüşmesi.

DoS sınırı testi kasıtlı olarak bir "decompression bomb" ÜRETMEZ; pypdf'in sınıra ulaştığında
attığı istisna tipinin (`PdfStreamError`, `PdfReadError` alt sınıfı) uygulamanın yakalama
sözleşmesine düştüğünü kanıtlar.
"""

import io
from unittest import mock

import pypdf
import pytest
from pypdf import PdfWriter
from pypdf.errors import PdfReadError, PdfStreamError
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.documents import pdf as pdf_module
from app.documents.pdf import PDFExtractionError, extract_pdf_text


def _pdf(pages: int = 1, text: str | None = None) -> bytes:
    """Geçerli bir PDF üretir; `text` verilirse ilk sayfaya metin içerik akışı ekler."""
    writer = PdfWriter()
    first = None
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        first = first or page
    if text is not None:
        font_ref = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }))
        first[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
        })
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 24 Tf 40 150 Td (" + text.encode("latin-1") + b") Tj ET")
        first[NameObject("/Contents")] = writer._add_object(stream)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pypdf_is_six_series() -> None:
    assert pypdf.__version__.split(".")[0] == "6"


def test_valid_single_page_text_is_extracted() -> None:
    result = extract_pdf_text(_pdf(text="MALIMUSAVIR PDF TEST"))
    assert result.page_count == 1
    assert "MALIMUSAVIR PDF TEST" in result.text


def test_multi_page_pdf_counts_all_pages() -> None:
    result = extract_pdf_text(_pdf(pages=3))
    assert result.page_count == 3
    assert result.text == ""  # boş sayfalar: metin yok (OCR yoluna düşer)


def test_invalid_content_raises_extraction_error() -> None:
    with pytest.raises(PDFExtractionError, match="Geçersiz"):
        extract_pdf_text(b"not a pdf")


def test_empty_content_raises_extraction_error() -> None:
    with pytest.raises(PDFExtractionError, match="boş"):
        extract_pdf_text(b"")


def test_pdf_stream_error_is_subclass_of_read_error() -> None:
    """pypdf 6.x DoS sınırları PdfStreamError atar; bu PdfReadError alt sınıfı olmalı ki
    extract_pdf_text'in `except (PdfReadError, ...)` cümleleri onu yakalasın."""
    assert issubclass(PdfStreamError, PdfReadError)


def test_dos_limit_on_open_is_handled_gracefully(monkeypatch) -> None:
    """Okuma sırasında bir DoS sınırı (PdfStreamError) çökme değil PDFExtractionError vermeli."""
    def boom(*args, **kwargs):
        raise PdfStreamError("decompressed size exceeds limit (test)")

    monkeypatch.setattr(pdf_module, "PdfReader", boom)
    with pytest.raises(PDFExtractionError, match="Geçersiz veya okunamayan PDF"):
        extract_pdf_text(_pdf(text="x"))


def test_dos_limit_during_text_extraction_is_handled_gracefully(monkeypatch) -> None:
    """Sayfa metni çıkarılırken oluşan DoS sınırı da güvenli biçimde dönüştürülmeli."""
    real_reader = pdf_module.PdfReader

    class LimitedPage:
        def extract_text(self, *a, **k):
            raise PdfStreamError("stream length exceeds limit (test)")

    class LimitedReader:
        def __init__(self, *a, **k):
            self._r = real_reader(*a, **k)

        @property
        def pages(self):
            return [LimitedPage()]

    monkeypatch.setattr(pdf_module, "PdfReader", LimitedReader)
    with pytest.raises(PDFExtractionError, match="PDF sayfası okunamadı"):
        extract_pdf_text(_pdf(text="x"))


def test_strict_mode_is_used() -> None:
    """Uygulama PdfReader'ı strict=True ile çağırmalı (bozuk PDF'leri erken reddetmek için)."""
    captured = {}
    real_reader = pdf_module.PdfReader

    def capturing(stream, *args, **kwargs):
        captured["strict"] = kwargs.get("strict", args[0] if args else None)
        return real_reader(stream, *args, **kwargs)

    with mock.patch.object(pdf_module, "PdfReader", side_effect=capturing):
        extract_pdf_text(_pdf(text="x"))
    assert captured["strict"] is True
