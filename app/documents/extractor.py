import asyncio
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.models import Belge
from app.documents.classifier import classify_document
from app.documents.expense_slip import expense_slip_metadata, parse_expense_slip
from app.documents.ocr import OCRDependencyError, extract_image_text
from app.documents.pdf import PDFExtractionError, extract_pdf_text
from app.documents.smm import parse_smm, smm_metadata
from app.documents.spreadsheet import SpreadsheetReadError, read_spreadsheet
from app.documents.storage import Storage
from app.documents.ubl import UBLParseError, parse_ubl_invoice
from app.documents.vision import VisionError, VisionResult, read_receipt_image, receipt_metadata
from app.documents.z_report import parse_z_report, z_report_metadata


class DocumentExtractionError(ValueError):
    pass


def _vision_classification(result: VisionResult) -> tuple[str, Decimal, list[str], str]:
    types = {doc.document_type for doc in result.documents}
    if types == {"z_raporu"}:
        category = "z_report"
    elif types <= {"perakende_fis", "pos_slip"}:
        category = "retail_receipt"
    else:
        category = "mixed_receipts"
    reasons = [
        f"{index}. belge: {reason}"
        for index, doc in enumerate(result.documents, start=1)
        for reason in doc.review_reasons
    ]
    needs_review = any(doc.review_status == "needs_review" for doc in result.documents)
    confidence = Decimal("0.5000") if needs_review else Decimal("0.9000")
    status = "needs_review" if needs_review else "classified"
    reasons = reasons or [f"{len(result.documents)} belge yerel modelle okundu, kontroller geçti."]
    return category, confidence, reasons, status


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _ubl_metadata(invoice: Any) -> dict[str, Any]:
    return _json_value(asdict(invoice))


async def extract_document(
    session: AsyncSession,
    storage: Storage,
    document: Belge,
) -> Belge:
    document.extraction_status = "processing"
    await session.commit()
    vision_result: VisionResult | None = None
    try:
        content = await storage.get(document.storage_key)
        if document.document_type == "ubl_xml":
            invoice = parse_ubl_invoice(content)
            document.extracted_text = None
            document.extraction_metadata = {
                "extractor": "ubl",
                "invoice": _ubl_metadata(invoice),
            }
        elif document.document_type == "pdf":
            result = extract_pdf_text(content)
            document.extracted_text = result.text
            document.extraction_metadata = {
                "extractor": "pypdf",
                "page_count": result.page_count,
            }
        elif document.document_type == "image" and get_settings().vision_backend == "ollama":
            vision_result = await asyncio.to_thread(read_receipt_image, content)
            document.extracted_text = None
            document.extraction_metadata = {
                "extractor": f"ollama:{vision_result.model}",
                "receipts": receipt_metadata(vision_result),
            }
        elif document.document_type == "image":
            result = extract_image_text(content)
            document.extracted_text = result.text
            document.extraction_metadata = {
                "extractor": "tesseract",
                "word_count": result.word_count,
                "average_confidence": result.average_confidence,
            }
        elif document.document_type == "spreadsheet":
            result = read_spreadsheet(content, document.original_filename)
            document.extracted_text = None
            document.extraction_metadata = {
                "extractor": "openpyxl/csv",
                "sheets": _json_value(asdict(result)),
            }
        else:
            raise DocumentExtractionError("Belge türü için çıkarıcı bulunamadı.")
    except (
        DocumentExtractionError,
        FileNotFoundError,
        OCRDependencyError,
        PDFExtractionError,
        SpreadsheetReadError,
        UBLParseError,
        VisionError,
    ) as exc:
        document.extraction_status = "failed"
        document.extraction_metadata = {"error": str(exc)}
        await session.commit()
        raise DocumentExtractionError(str(exc)) from exc
    document.extraction_status = "completed"
    if vision_result is not None:
        category, confidence, reasons, status = _vision_classification(vision_result)
        document.document_category = category
        document.classification_confidence = confidence
        document.classification_reasons = reasons
        document.classification_status = status
        await session.commit()
        await session.refresh(document)
        return document
    classification = classify_document(
        document.document_type,
        document.extracted_text,
        document.extraction_metadata,
    )
    document.classification_status = classification.status
    document.document_category = classification.category
    document.classification_confidence = classification.confidence
    document.classification_reasons = list(classification.reasons)
    if classification.category == "z_report" and document.extracted_text:
        report = parse_z_report(document.extracted_text)
        document.extraction_metadata = {
            **document.extraction_metadata,
            "z_report": z_report_metadata(report),
        }
        if report.review_status == "needs_review":
            document.classification_status = "needs_review"
            document.classification_reasons = list(report.review_reasons)
    if classification.category == "expense_slip" and document.extracted_text:
        slip = parse_expense_slip(document.extracted_text)
        document.extraction_metadata = {
            **document.extraction_metadata,
            "expense_slip": expense_slip_metadata(slip),
        }
        if slip.review_status == "needs_review":
            document.classification_status = "needs_review"
            document.classification_reasons = list(slip.review_reasons)
    if classification.category == "smm" and document.extracted_text:
        smm = parse_smm(document.extracted_text)
        document.extraction_metadata = {
            **document.extraction_metadata,
            "smm": smm_metadata(smm),
        }
        if smm.review_status == "needs_review":
            document.classification_status = "needs_review"
            document.classification_reasons = list(smm.review_reasons)
    await session.commit()
    await session.refresh(document)
    return document
