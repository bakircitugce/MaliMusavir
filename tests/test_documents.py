from io import BytesIO
from decimal import Decimal

import pytest
from openpyxl import Workbook

from app.documents.service import detect_document_type, validate_document_content
from app.documents.pdf import PDFExtractionError, extract_pdf_text
from app.documents.ocr import OCRExtractionError, extract_image_text
from app.documents.extractor import _json_value
from app.documents.classifier import classify_document
from app.documents.review import DocumentReviewInput, apply_document_review
from app.documents.z_report import parse_z_report
from app.documents.expense_slip import parse_expense_slip
from app.documents.smm import parse_smm
from app.documents.router import serialize_document
from app.documents.spreadsheet import read_spreadsheet
from app.documents.ubl import UBLParseError, parse_ubl_invoice

UBL_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ProfileID> temelfatura </cbc:ProfileID>
  <cbc:ID>ABC2026000000001</cbc:ID>
  <cbc:IssueDate>2026-11-03</cbc:IssueDate>
  <cbc:InvoiceTypeCode>SATIS</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>TRY</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyName><cbc:Name>Satıcı A.Ş.</cbc:Name></cac:PartyName>
    <cac:PartyTaxScheme><cbc:CompanyID schemeID="VKN">1234567890</cbc:CompanyID></cac:PartyTaxScheme>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
    <cac:PartyLegalEntity><cbc:RegistrationName>Alıcı Ltd.</cbc:RegistrationName></cac:PartyLegalEntity>
    <cac:PartyTaxScheme><cbc:CompanyID schemeID="VKN">0987654321</cbc:CompanyID></cac:PartyTaxScheme>
  </cac:Party></cac:AccountingCustomerParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="TRY">20.00</cbc:TaxAmount></cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount currencyID="TRY">100.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="TRY">120.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="TRY">120.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID><cbc:InvoicedQuantity unitCode="C62">2</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="TRY">100.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Description>Hizmet</cbc:Description>
      <cac:ClassifiedTaxCategory><cbc:Percent>20</cbc:Percent></cac:ClassifiedTaxCategory>
      <cac:SellersItemIdentification><cbc:ID>H-1</cbc:ID></cac:SellersItemIdentification>
    </cac:Item>
  </cac:InvoiceLine>
</Invoice>""".encode("utf-8")


def test_document_type_detection_supports_phase_one_formats() -> None:
    assert detect_document_type("fatura.xml", "application/octet-stream") == "ubl_xml"
    assert detect_document_type("fatura.pdf", "application/octet-stream") == "pdf"
    assert detect_document_type("foto.jpg", "image/jpeg") == "image"
    assert detect_document_type("ekstre.xlsx", "application/octet-stream") == "spreadsheet"


def test_document_type_detection_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="Desteklenmeyen"):
        detect_document_type("notlar.txt", "text/plain")


def test_document_content_validation_checks_signatures() -> None:
    validate_document_content("pdf", b"%PDF-1.7")
    validate_document_content("image", b"\x89PNG\r\n\x1a\nrest")
    with pytest.raises(ValueError, match="PDF"):
        validate_document_content("pdf", b"not pdf")
    with pytest.raises(ValueError, match="UBL"):
        validate_document_content("ubl_xml", b"<Root/>")


def test_ubl_parser_extracts_invoice_and_lines() -> None:
    invoice = parse_ubl_invoice(UBL_SAMPLE)

    assert invoice.invoice_id == "ABC2026000000001"
    assert invoice.issue_date == "2026-11-03"
    assert invoice.currency == "TRY"
    assert invoice.supplier.name == "Satıcı A.Ş."
    assert invoice.supplier.tax_id == "1234567890"
    assert invoice.customer.name == "Alıcı Ltd."
    assert invoice.tax_total == 20
    assert invoice.payable_amount == 120
    assert invoice.lines[0].description == "Hizmet"
    assert invoice.lines[0].quantity == 2
    assert invoice.lines[0].tax_percent == 20


def test_ubl_parser_rejects_invalid_or_unsafe_xml() -> None:
    with pytest.raises(UBLParseError, match="UBL Invoice"):
        parse_ubl_invoice(b"<NotInvoice/>")
    with pytest.raises(UBLParseError, match="DTD"):
        parse_ubl_invoice(b"<!DOCTYPE foo><Invoice/>")


def test_pdf_extractor_rejects_invalid_content() -> None:
    with pytest.raises(PDFExtractionError, match="Geçersiz"):
        extract_pdf_text(b"not a pdf")


def test_csv_reader_preserves_turkish_headers_and_values() -> None:
    data = read_spreadsheet("Hesap;Tutar\nKasa;1.200,50\n".encode("cp1254"), "ekstre.csv")

    assert data.sheets[0].headers == ("Hesap", "Tutar")
    assert data.sheets[0].rows[0]["Hesap"] == "Kasa"
    assert data.sheets[0].rows[0]["Tutar"] == "1.200,50"


def test_xlsx_reader_reads_all_sheets_and_normalizes_dates() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Hareketler"
    sheet.append(["Tarih", "Tutar", "Tutar"])
    sheet.append([None, 12.5, "not"])
    second = workbook.create_sheet("Özet")
    second.append(["Durum"])
    second.append(["Hazır"])
    buffer = BytesIO()
    workbook.save(buffer)

    data = read_spreadsheet(buffer.getvalue(), "ekstre.xlsx")

    assert [sheet.name for sheet in data.sheets] == ["Hareketler", "Özet"]
    assert data.sheets[0].headers == ("Tarih", "Tutar", "Tutar_2")
    assert data.sheets[0].rows[0]["Tutar"] == 12.5
    assert data.sheets[1].rows[0]["Durum"] == "Hazır"


def test_ocr_rejects_invalid_image() -> None:
    with pytest.raises(OCRExtractionError, match="Geçersiz"):
        extract_image_text(b"not an image")


def test_document_list_response_does_not_expose_storage_key() -> None:
    class Document:
        id = "document-id"
        original_filename = "fatura.pdf"
        content_type = "application/pdf"
        size_bytes = 12
        sha256 = "a" * 64
        document_type = "pdf"
        status = "received"
        extraction_status = "pending"
        classification_status = "pending"
        document_category = None
        classification_confidence = None
        review_status = "pending"
        storage_key = "private/path"

    assert "storage_key" not in serialize_document(Document())


def test_extraction_metadata_converts_decimal_values_to_json_strings() -> None:
    from decimal import Decimal

    assert _json_value({"amount": Decimal("12.30")}) == {"amount": "12.30"}


def test_classifier_marks_ubl_and_known_text() -> None:
    ubl = classify_document("ubl_xml", None, {})
    assert ubl.category == "invoice"
    assert ubl.status == "classified"

    z_report = classify_document("pdf", "Z RAPORU Z NO ÖKC", {})
    assert z_report.category == "z_report"
    assert z_report.status == "classified"


def test_classifier_sends_unknown_document_to_review() -> None:
    result = classify_document("image", "rastgele metin", {})
    assert result.category == "unknown"
    assert result.status == "needs_review"


def test_document_review_approves_and_overrides_classification() -> None:
    class Document:
        document_category = "unknown"
        review_status = "pending"
        review_note = None
        classification_status = "needs_review"
        classification_confidence = Decimal("0.10")
        classification_reasons = []

    document = apply_document_review(
        Document(),
        DocumentReviewInput(category="expense_slip", note="Gider pusulası olarak doğrulandı."),
    )

    assert document.document_category == "expense_slip"
    assert document.review_status == "approved"
    assert document.classification_confidence == Decimal("1.0000")


def test_z_report_parser_extracts_totals_and_validates_payment_split() -> None:
    report = parse_z_report(
        "Z RAPORU 2026-11-03 Z NO: 42 ÖKC SİCİL NO: ABC123 "
        "TOPLAM: 12.000,00 NAKİT: 4.800,00 KREDİ KARTI: 7.200,00"
    )

    assert report.report_date == "2026-11-03"
    assert report.report_number == "42"
    assert report.fiscal_id == "ABC123"
    assert report.total == Decimal("12000.00")
    assert report.cash_total == Decimal("4800.00")
    assert report.card_total == Decimal("7200.00")
    assert report.review_status == "ready"


def test_z_report_parser_flags_incomplete_or_unbalanced_reports() -> None:
    report = parse_z_report("Z RAPORU TOPLAM: 1.000,00 NAKİT: 900,00")

    assert report.review_status == "needs_review"
    assert "Tarih bulunamadı" in report.review_reasons
    assert "Ödeme dağılımı bulunamadı" not in report.review_reasons


def test_expense_slip_parser_extracts_and_balances_amounts() -> None:
    slip = parse_expense_slip(
        "GİDER PUSULASI 2026-11-03 SATICI: Ahmet Yılmaz VKN: 1234567890 "
        "AÇIKLAMA: Nakliye MATRAH: 1.000,00 KDV: 200,00 TOPLAM: 1.200,00"
    )

    assert slip.seller_name == "Ahmet Yılmaz"
    assert slip.seller_tax_id == "1234567890"
    assert slip.base_amount == Decimal("1000.00")
    assert slip.vat_amount == Decimal("200.00")
    assert slip.total_amount == Decimal("1200.00")
    assert slip.review_status == "ready"


def test_smm_parser_extracts_and_flags_mismatch() -> None:
    smm = parse_smm(
        "SERBEST MESLEK MAKBUZ NO: S-42 2026-11-03 "
        "DÜZENLEYEN: Mali Danışman VKN: 1234567890 "
        "MÜŞTERİ: Demo AŞ BRÜT: 1.000,00 STOPAJ: 200,00 KDV: 200,00 NET: 1.000,00"
    )

    assert smm.receipt_number == "S-42"
    assert smm.professional_name == "Mali Danışman"
    assert smm.client_name == "Demo AŞ"
    assert smm.review_status == "ready"

    mismatch = parse_smm("SMM 2026-11-03 BRUT: 1.000,00 STOPAJ: 100,00 NET: 800,00")
    assert mismatch.review_status == "needs_review"
