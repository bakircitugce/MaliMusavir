from datetime import datetime, timezone
from uuid import uuid4

from app.einvoice.adapter import (
    EInvoiceAdapter,
    EInvoiceDocument,
    EInvoiceDocumentStatus,
    EInvoiceQuery,
    EInvoiceResult,
)


class FakeEInvoiceAdapter(EInvoiceAdapter):
    def __init__(self) -> None:
        self._documents: dict[str, EInvoiceDocument] = {}
        self._statuses: dict[str, str] = {}

    async def fetch_incoming(self, query: EInvoiceQuery) -> EInvoiceResult:
        return self._page(query)

    async def fetch_outgoing(self, query: EInvoiceQuery) -> EInvoiceResult:
        return self._page(query)

    async def issue_invoice(self, ubl_document: bytes) -> EInvoiceDocument:
        if not ubl_document.strip():
            raise ValueError("Gönderilecek UBL belgesi boş olamaz.")
        ettn = str(uuid4())
        document = EInvoiceDocument(
            provider_id=f"fake-{ettn}",
            ettn=ettn,
            invoice_number=f"FAKE-{len(self._documents) + 1:06d}",
            issue_date=datetime.now(timezone.utc).date(),
            supplier_tax_id="0000000000",
            supplier_name="Fake Sağlayıcı",
            buyer_tax_id="0000000000",
            buyer_name="Fake Alıcı",
            payable_amount=0,
            currency="TRY",
            raw_ubl=ubl_document,
        )
        self._documents[ettn] = document
        self._statuses[ettn] = "submitted"
        return document

    async def get_status(self, ettn: str) -> EInvoiceDocumentStatus:
        return self._status(ettn)

    async def cancel_or_dispute(self, ettn: str, reason: str) -> EInvoiceDocumentStatus:
        if not reason.strip():
            raise ValueError("İptal/itiraz gerekçesi boş olamaz.")
        status = self._status(ettn)
        self._statuses[ettn] = "cancel_requested"
        return EInvoiceDocumentStatus(
            provider_id=status.provider_id,
            ettn=ettn,
            status="cancel_requested",
            updated_at=datetime.now(timezone.utc),
        )

    def _page(self, query: EInvoiceQuery) -> EInvoiceResult:
        documents = tuple(self._documents.values())
        start = (query.page - 1) * query.page_size
        end = start + query.page_size
        return EInvoiceResult(
            documents=documents[start:end],
            page=query.page,
            page_size=query.page_size,
            has_more=end < len(documents),
        )

    def _status(self, ettn: str) -> EInvoiceDocumentStatus:
        document = self._documents.get(ettn)
        if document is None:
            raise ValueError("e-Fatura bulunamadı.")
        return EInvoiceDocumentStatus(
            provider_id=document.provider_id,
            ettn=ettn,
            status=self._statuses[ettn],
            updated_at=datetime.now(timezone.utc),
        )
