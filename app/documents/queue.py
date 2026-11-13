import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

from app.core.db import session_factory
from app.core.models import Belge
from app.documents.extractor import extract_document
from app.documents.storage import LocalStorage
from app.ledger.posting import post_document

logger = logging.getLogger(__name__)


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
        try:
            await extract_document(session, LocalStorage(), document)
        except ValueError:
            pass  # okunamayan belge de kuyruğa "elle giriş gerekli" olarak düşer
        await process_after_extraction(session, document)


async def process_after_extraction(session, document: Belge) -> None:
    """Okuma sonrası: TDHP kurallarıyla taslak yevmiye üret (hata kuyruğu durdurmaz)."""
    try:
        await session.refresh(document)
        await post_document(session, document)
    except Exception:  # noqa: BLE001
        logger.exception("Taslak yevmiye üretilemedi: %s", document.id)
        await session.rollback()


def enqueue_extraction(document_id: UUID, buro_id: UUID, mukellef_id: UUID) -> None:
    """Run locally until the durable Celery/Redis worker is introduced."""
    asyncio.create_task(_run_extraction(document_id, buro_id, mukellef_id))
