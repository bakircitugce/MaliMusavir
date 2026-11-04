from app.documents.ubl import UBLParseError, parse_ubl_invoice
from app.einvoice.adapter import EInvoiceAdapter, EInvoiceDocument


async def submit_ubl_invoice(
    adapter: EInvoiceAdapter,
    ubl_document: bytes,
    *,
    approved: bool = False,
) -> EInvoiceDocument:
    if not approved:
        raise ValueError("e-Fatura gönderimi için açık onay gereklidir.")
    try:
        invoice = parse_ubl_invoice(ubl_document)
    except UBLParseError as exc:
        raise ValueError(f"Gönderilecek UBL doğrulanamadı: {exc}") from exc
    if invoice.payable_amount is None:
        raise ValueError("Gönderilecek UBL belgesinde ödenecek tutar bulunmalıdır.")
    if invoice.payable_amount < 0:
        raise ValueError("Gönderilecek UBL tutarı negatif olamaz.")
    if not invoice.currency:
        raise ValueError("Gönderilecek UBL belgesinde para birimi bulunmalıdır.")
    return await adapter.issue_invoice(ubl_document)
