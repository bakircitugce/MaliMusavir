from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class EInvoiceQuery:
    start_date: date
    end_date: date
    page: int = 1
    page_size: int = 100

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError("e-Fatura sorgu tarih aralığı geçersiz.")
        if self.page < 1:
            raise ValueError("e-Fatura sorgu sayfası 1 veya daha büyük olmalıdır.")
        if self.page_size < 1 or self.page_size > 500:
            raise ValueError("e-Fatura sayfa boyutu 1 ile 500 arasında olmalıdır.")


@dataclass(frozen=True)
class EInvoiceDocument:
    provider_id: str
    ettn: str
    invoice_number: str
    issue_date: date
    supplier_tax_id: str
    supplier_name: str
    buyer_tax_id: str
    buyer_name: str
    payable_amount: Decimal
    currency: str
    raw_ubl: bytes | None = None

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or not self.ettn.strip():
            raise ValueError("e-Fatura sağlayıcı kimliği ve ETTN zorunludur.")
        if self.payable_amount < 0:
            raise ValueError("e-Fatura tutarı negatif olamaz.")
        if len(self.raw_ubl or b"") > 25 * 1024 * 1024:
            raise ValueError("e-Fatura UBL içeriği 25 MB sınırını aşamaz.")


@dataclass(frozen=True)
class EInvoiceResult:
    documents: tuple[EInvoiceDocument, ...]
    page: int
    page_size: int
    has_more: bool


@dataclass(frozen=True)
class EInvoiceDocumentStatus:
    provider_id: str
    ettn: str
    status: str
    updated_at: datetime


class EInvoiceAdapter(Protocol):
    async def fetch_incoming(self, query: EInvoiceQuery) -> EInvoiceResult:
        ...

    async def fetch_outgoing(self, query: EInvoiceQuery) -> EInvoiceResult:
        ...

    async def issue_invoice(self, ubl_document: bytes) -> EInvoiceDocument:
        ...

    async def get_status(self, ettn: str) -> EInvoiceDocumentStatus:
        ...

    async def cancel_or_dispute(self, ettn: str, reason: str) -> EInvoiceDocumentStatus:
        ...
