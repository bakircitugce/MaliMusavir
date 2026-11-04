from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.models import Belge


DocumentCategory = Literal[
    "invoice",
    "z_report",
    "bank_statement",
    "expense_slip",
    "smm",
    "unknown",
]


class DocumentReviewInput(BaseModel):
    category: DocumentCategory
    status: Literal["approved", "needs_review"] = "approved"
    note: str | None = Field(default=None, max_length=2000)


def apply_document_review(document: Belge, review: DocumentReviewInput) -> Belge:
    document.document_category = review.category
    document.review_status = review.status
    document.review_note = review.note
    if review.status == "approved":
        document.classification_status = "classified"
        document.classification_confidence = Decimal("1.0000")
        document.classification_reasons = ["Mali müşavir tarafından onaylandı"]
    else:
        document.classification_status = "needs_review"
        document.classification_reasons = ["Mali müşavir incelemesi bekleniyor"]
    return document
