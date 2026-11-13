import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

from app.bank.statement_import import IbanRequiredError, import_statement
from app.bank.statement_parser import StatementParseError
from app.core.db import session_factory
from app.core.models import Belge
from app.documents.extractor import extract_document
from app.documents.storage import LocalStorage, Storage
from app.ledger.draft import LedgerValidationError
from app.ledger.posting import PostingResult, post_document

logger = logging.getLogger(__name__)

# Ekstre olabilecek belge türleri: önce banka ekstresi olarak okunmaya çalışılır.
STATEMENT_CANDIDATE_TYPES = {"spreadsheet", "pdf"}


async def _run_extraction(document_id: UUID, buro_id: UUID, mukellef_id: UUID) -> None:
    async with session_factory() as session:
        document = await session.scalar(
            select(Belge).where(
                Belge.id == document_id,
                Belge.buro_id == buro_id,
                Belge.mukellef_id == mukellef_id,
            )
        )
        if document is None:
            return
        storage = LocalStorage()
        try:
            await extract_document(session, storage, document)
        except ValueError:
            pass  # okunamayan belge de kuyruğa "elle giriş gerekli" olarak düşer
        await process_after_extraction(session, document, storage)


async def _try_bank_statement(session, document: Belge, storage: Storage) -> bool:
    """Belge bir banka ekstresiyse hareketleri içe aktarır. Ekstre değilse False döner."""
    try:
        content = await storage.get(document.storage_key)
        result = await import_statement(
            session,
            buro_id=document.buro_id,
            mukellef_id=document.mukellef_id,
            content=content,
            filename=document.original_filename,
        )
    except IbanRequiredError as exc:
        posting = PostingResult(
            "needs_review",
            [f"{exc} Banka & POS sekmesinden IBAN belirterek yükleyin."],
        )
        document.document_category = "bank_statement"
        document.extraction_metadata = {
            **(document.extraction_metadata or {}), "posting": posting.metadata()
        }
        document.review_status = "needs_review"
        await session.commit()
        return True
    except (StatementParseError, LedgerValidationError, FileNotFoundError):
        await session.rollback()
        return False
    posting = PostingResult("bank_imported", [result.summary(), *result.warnings])
    document.document_category = "bank_statement"
    document.extraction_metadata = {
        **(document.extraction_metadata or {}),
        "posting": posting.metadata(),
        "bank_import": result.as_dict(),
    }
    # Hareketler Banka & POS ekranında eşleştirilir; belge onay kuyruğundan çıkar.
    document.review_status = "approved" if not result.balance_mismatches else "needs_review"
    await session.commit()
    return True


async def process_after_extraction(session, document: Belge, storage: Storage | None = None) -> None:
    """Okuma sonrası: ekstre ise bankaya aktar, değilse TDHP taslak yevmiye üret."""
    try:
        await session.refresh(document)
        meta = document.extraction_metadata or {}
        if (
            document.document_type in STATEMENT_CANDIDATE_TYPES
            and "invoice" not in meta
            and await _try_bank_statement(session, document, storage or LocalStorage())
        ):
            return
        await post_document(session, document)
    except Exception:  # noqa: BLE001
        logger.exception("Belge sonrası işlem yapılamadı: %s", document.id)
        await session.rollback()


def enqueue_extraction(document_id: UUID, buro_id: UUID, mukellef_id: UUID) -> None:
    """Run locally until the durable Celery/Redis worker is introduced."""
    asyncio.create_task(_run_extraction(document_id, buro_id, mukellef_id))
