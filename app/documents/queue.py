import asyncio
from uuid import UUID

from sqlalchemy import select

from app.core.db import session_factory
from app.core.models import Belge
from app.documents.extractor import extract_document
from app.documents.storage import LocalStorage


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
            return


def enqueue_extraction(document_id: UUID, buro_id: UUID, mukellef_id: UUID) -> None:
    """Run locally until the durable Celery/Redis worker is introduced."""
    asyncio.create_task(_run_extraction(document_id, buro_id, mukellef_id))
