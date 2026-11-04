from datetime import date

import pytest

from app.einvoice import EInvoiceQuery, fetch_all_invoice_pages, submit_ubl_invoice
from app.einvoice.adapter import EInvoiceResult
from app.einvoice.fake import FakeEInvoiceAdapter
from app.einvoice.registry import EInvoiceAdapterRegistry


def test_einvoice_query_validates_pagination_and_dates() -> None:
    with pytest.raises(ValueError, match="tarih aralığı"):
        EInvoiceQuery(date(2026, 11, 4), date(2026, 11, 3))
    with pytest.raises(ValueError, match="sayfa boyutu"):
        EInvoiceQuery(date(2026, 11, 3), date(2026, 11, 3), page_size=501)


@pytest.mark.asyncio
async def test_fake_einvoice_adapter_issue_status_and_cancel() -> None:
    adapter = FakeEInvoiceAdapter()
    document = await adapter.issue_invoice(b"<Invoice />")

    status = await adapter.get_status(document.ettn)
    assert status.status == "submitted"

    cancelled = await adapter.cancel_or_dispute(document.ettn, "Test gerekçesi")
    assert cancelled.status == "cancel_requested"


@pytest.mark.asyncio
async def test_fake_einvoice_adapter_rejects_empty_ubl() -> None:
    with pytest.raises(ValueError, match="UBL"):
        await FakeEInvoiceAdapter().issue_invoice(b" ")


@pytest.mark.asyncio
async def test_submit_ubl_invoice_validates_before_adapter_call() -> None:
    ubl = b"""
    <Invoice>
      <ID>FAT-001</ID>
      <IssueDate>2026-11-03</IssueDate>
      <DocumentCurrencyCode>TRY</DocumentCurrencyCode>
      <LegalMonetaryTotal>
        <PayableAmount>120.00</PayableAmount>
      </LegalMonetaryTotal>
    </Invoice>
    """

    document = await submit_ubl_invoice(FakeEInvoiceAdapter(), ubl, approved=True)

    assert document.raw_ubl == ubl


@pytest.mark.asyncio
async def test_submit_ubl_invoice_rejects_missing_payable_amount() -> None:
    with pytest.raises(ValueError, match="ödenecek tutar"):
        await submit_ubl_invoice(
            FakeEInvoiceAdapter(),
            b"<Invoice><ID>FAT-002</ID><IssueDate>2026-11-03</IssueDate>"
            b"<DocumentCurrencyCode>TRY</DocumentCurrencyCode></Invoice>",
            approved=True,
        )


@pytest.mark.asyncio
async def test_submit_ubl_invoice_requires_explicit_approval_before_adapter_call() -> None:
    with pytest.raises(ValueError, match="açık onay"):
        await submit_ubl_invoice(
            FakeEInvoiceAdapter(),
            b"<Invoice />",
        )


def test_einvoice_registry_requires_explicit_provider() -> None:
    registry = EInvoiceAdapterRegistry()
    registry.register(" Fake ", FakeEInvoiceAdapter)

    assert registry.providers() == ("fake",)
    assert isinstance(registry.create("FAKE"), FakeEInvoiceAdapter)
    with pytest.raises(LookupError, match="Desteklenmeyen"):
        registry.create("unknown")
    with pytest.raises(ValueError, match="zaten kayıtlı"):
        registry.register("fake", FakeEInvoiceAdapter)


@pytest.mark.asyncio
async def test_fetch_all_invoice_pages_stops_after_provider_completion() -> None:
    adapter = FakeEInvoiceAdapter()
    query = EInvoiceQuery(date(2026, 11, 3), date(2026, 11, 3))

    documents = await fetch_all_invoice_pages(adapter.fetch_incoming, query)

    assert documents == ()


@pytest.mark.asyncio
async def test_fetch_all_invoice_pages_rejects_invalid_provider_page() -> None:
    query = EInvoiceQuery(date(2026, 11, 3), date(2026, 11, 3))

    async def invalid_page(request: EInvoiceQuery) -> EInvoiceResult:
        return EInvoiceResult((), request.page + 1, request.page_size, False)

    with pytest.raises(ValueError, match="sayfalama"):
        await fetch_all_invoice_pages(invalid_page, query)


@pytest.mark.asyncio
async def test_fetch_all_invoice_pages_deduplicates_ettn_across_pages() -> None:
    adapter = FakeEInvoiceAdapter()
    first = await adapter.issue_invoice(b"<Invoice><ID>1</ID></Invoice>")

    async def repeated_page(request: EInvoiceQuery) -> EInvoiceResult:
        if request.page == 1:
            return EInvoiceResult((first,), 1, request.page_size, True)
        return EInvoiceResult((first,), 2, request.page_size, False)

    documents = await fetch_all_invoice_pages(
        repeated_page,
        EInvoiceQuery(date(2026, 11, 3), date(2026, 11, 3)),
    )

    assert documents == (first,)
