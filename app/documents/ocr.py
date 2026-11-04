from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError
import pytesseract
from pytesseract import Output
from pytesseract.pytesseract import TesseractError, TesseractNotFoundError


class OCRExtractionError(ValueError):
    pass


class OCRDependencyError(OCRExtractionError):
    pass


@dataclass(frozen=True)
class OCRText:
    text: str
    word_count: int
    average_confidence: float | None


def extract_image_text(content: bytes, language: str = "tur+eng") -> OCRText:
    if not content:
        raise OCRExtractionError("Görüntü içeriği boş olamaz.")
    try:
        image = Image.open(BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise OCRExtractionError("Geçersiz veya okunamayan görüntü.") from exc

    try:
        data = pytesseract.image_to_data(image, lang=language, output_type=Output.DICT)
    except (TesseractNotFoundError, TesseractError) as exc:
        raise OCRDependencyError(
            "Tesseract kurulu değil veya istenen dil verisi bulunamadı."
        ) from exc

    words: list[str] = []
    confidences: list[float] = []
    for text, raw_confidence in zip(data["text"], data["conf"]):
        word = text.strip()
        if not word:
            continue
        words.append(word)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if confidence >= 0:
            confidences.append(confidence)
    return OCRText(
        text=" ".join(words),
        word_count=len(words),
        average_confidence=(
            round(sum(confidences) / len(confidences), 2) if confidences else None
        ),
    )
