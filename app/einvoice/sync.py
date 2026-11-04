from collections.abc import Awaitable, Callable

from app.einvoice.adapter import EInvoiceDocument, EInvoiceQuery, EInvoiceResult


FetchPage = Callable[[EInvoiceQuery], Awaitable[EInvoiceResult]]


async def fetch_all_invoice_pages(
    fetch_page: FetchPage,
    query: EInvoiceQuery,
    *,
    max_pages: int = 100,
) -> tuple[EInvoiceDocument, ...]:
    if max_pages < 1:
        raise ValueError("e-Fatura azami sayfa sayısı 1 veya daha büyük olmalıdır.")
    documents: list[EInvoiceDocument] = []
    current_page = query.page
    for _ in range(max_pages):
        page_query = EInvoiceQuery(
            start_date=query.start_date,
            end_date=query.end_date,
            page=current_page,
            page_size=query.page_size,
        )
        result = await fetch_page(page_query)
        if result.page != current_page or result.page_size != query.page_size:
            raise ValueError("e-Fatura sağlayıcısı geçersiz sayfalama yanıtı döndürdü.")
        known_ettns = {document.ettn for document in documents}
        documents.extend(
            document for document in result.documents if document.ettn not in known_ettns
        )
        if not result.has_more:
            return tuple(documents)
        current_page += 1
    raise RuntimeError("e-Fatura sayfalama azami sayfa sınırını aştı.")
