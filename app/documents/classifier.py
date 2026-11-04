import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClassificationResult:
    category: str
    confidence: float
    reasons: tuple[str, ...]
    status: str


RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("z_report", ("z raporu", "z no", "ö.k.c", "ökc", "yazar kasa")),
    ("bank_statement", ("banka ekstresi", "işlem tarihi", "iban", "bakiye", "dekont")),
    ("expense_slip", ("gider pusulası", "gider pusulasi")),
    ("smm", ("serbest meslek makbuz", "smm", "meslek makbuz")),
    ("invoice", ("fatura", "invoice", "e-fatura", "e-arşiv", "ettn", "matrah")),
)


def _text_from_metadata(metadata: dict[str, Any]) -> str:
    invoice = metadata.get("invoice")
    if isinstance(invoice, dict):
        values = [str(value) for value in invoice.values() if value is not None]
        return " ".join(values)
    return ""


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).replace(
        "ı", "i"
    )


def classify_document(
    document_type: str,
    extracted_text: str | None,
    metadata: dict[str, Any] | None = None,
) -> ClassificationResult:
    text = _normalize(" ".join(
        part for part in (extracted_text or "", _text_from_metadata(metadata or {})) if part
    ))
    scores = {
        category: sum(
            1
            for keyword in keywords
            if re.search(rf"(?<!\w){re.escape(_normalize(keyword))}(?!\w)", text)
        )
        for category, keywords in RULES
    }
    category, score = max(scores.items(), key=lambda item: item[1], default=("unknown", 0))
    if document_type == "ubl_xml" and score == 0:
        return ClassificationResult("invoice", 0.95, ("UBL Invoice formatı",), "classified")
    if score == 0:
        return ClassificationResult("unknown", 0.0, ("Eşleşen sınıflandırma kuralı yok",), "needs_review")
    keyword_count = len(dict(RULES)[category])
    confidence = round(min(0.95, 0.55 + score / keyword_count * 0.4), 2)
    status = "classified" if confidence >= 0.75 else "needs_review"
    return ClassificationResult(
        category,
        confidence,
        (f"{score} anahtar kelime eşleşti",),
        status,
    )
