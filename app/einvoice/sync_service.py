from uuid import UUID
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.extractor import extract_document
from app.documents.service import DuplicateDocumentError, receive_document
from app.documents.storage import Storage, LocalStorage
from app.einvoice.adapter import EInvoiceAdapter, EInvoiceQuery
from app.einvoice.sync import fetch_all_invoice_pages


@dataclass(frozen=True)
class EInvoiceSyncResult:
    total_fetched: int
    imported_count: int
    skipped_count: int
    extracted_count: int


async def sync_incoming_invoices(
    session: AsyncSession,
    adapter: EInvoiceAdapter,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    query: EInvoiceQuery,
    storage: Storage | None = None,
) -> EInvoiceSyncResult:
    """
    e-Fatura entegratöründen gelen faturaları çeker, mükerrerleri atlar
    ve yeni gelen faturaları Belge havuzuna aktarıp otomatik ayrıştırır (extract).
    """
    if storage is None:
        storage = LocalStorage()

    documents = await fetch_all_invoice_pages(adapter.fetch_incoming, query)
    total_fetched = len(documents)
    imported_count = 0
    skipped_count = 0
    extracted_count = 0

    for doc in documents:
        if not doc.raw_ubl:
            skipped_count += 1
            continue

        filename = f"{doc.invoice_number or doc.ettn}.xml"
        try:
            belge = await receive_document(
                session=session,
                storage=storage,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                filename=filename,
                content_type="application/xml",
                content=doc.raw_ubl,
            )
            imported_count += 1
            try:
                await extract_document(
                    session=session,
                    storage=storage,
                    document_id=belge.id,
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                )
                extracted_count += 1
            except Exception:
                # Ayrıştırma hatası olsa bile belge kaydedilmiş kalır
                pass
        except DuplicateDocumentError:
            skipped_count += 1
        except Exception:
            skipped_count += 1

    return EInvoiceSyncResult(
        total_fetched=total_fetched,
        imported_count=imported_count,
        skipped_count=skipped_count,
        extracted_count=extracted_count,
    )
