from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PDFExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class PDFText:
    page_count: int
    text: str


def extract_pdf_text(content: bytes) -> PDFText:
    if not content:
        raise PDFExtractionError("PDF içeriği boş olamaz.")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
    except (PdfReadError, OSError, ValueError) as exc:
        raise PDFExtractionError("Geçersiz veya okunamayan PDF.") from exc

    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except (PdfReadError, ValueError) as exc:
            raise PDFExtractionError("PDF sayfası okunamadı.") from exc
    return PDFText(page_count=len(reader.pages), text="\n".join(pages).strip())
