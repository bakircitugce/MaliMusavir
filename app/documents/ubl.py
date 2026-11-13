from decimal import Decimal, InvalidOperation
from typing import Final
from xml.etree import ElementTree

from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException

from app.documents.extraction import InvoiceData, InvoiceLineData, PartyData

INVOICE_ROOT: Final = "Invoice"


class UBLParseError(ValueError):
    pass


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(element) if _local_name(child.tag) == name]


def _first(element: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    current = [element]
    for name in names:
        next_elements: list[ElementTree.Element] = []
        for parent in current:
            next_elements.extend(_children(parent, name))
        current = next_elements
        if not current:
            return None
    return current[0]


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _required_text(element: ElementTree.Element, *names: str) -> str:
    value = _text(_first(element, *names))
    if value is None:
        raise UBLParseError(f"Zorunlu UBL alanı eksik: {'/'.join(names)}")
    return value


def _decimal(element: ElementTree.Element | None, field_name: str) -> Decimal | None:
    value = _text(element)
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise UBLParseError(f"Geçersiz sayısal UBL alanı: {field_name}") from exc


def _party(invoice: ElementTree.Element, party_name: str) -> PartyData:
    party = _first(invoice, party_name, "Party")
    if party is None:
        return PartyData(name=None, tax_id=None, tax_id_scheme=None)
    name = _text(_first(party, "PartyName", "Name")) or _text(
        _first(party, "PartyLegalEntity", "RegistrationName")
    )
    tax_scheme = _first(party, "PartyTaxScheme")
    tax_id = _text(_first(tax_scheme, "CompanyID")) if tax_scheme is not None else None
    tax_id_scheme = (
        _first(tax_scheme, "CompanyID").attrib.get("schemeID")
        if tax_scheme is not None and _first(tax_scheme, "CompanyID") is not None
        else None
    )
    return PartyData(name=name, tax_id=tax_id, tax_id_scheme=tax_id_scheme)


def _line(line: ElementTree.Element) -> InvoiceLineData:
    quantity = _first(line, "InvoicedQuantity")
    line_amount = _first(line, "LineExtensionAmount")
    tax_category = _first(line, "Item", "ClassifiedTaxCategory")
    item_code = _text(_first(line, "Item", "SellersItemIdentification", "ID"))
    return InvoiceLineData(
        line_id=_text(_first(line, "ID")),
        description=_text(_first(line, "Item", "Description"))
        or _text(_first(line, "Item", "Name")),
        quantity=_decimal(quantity, "InvoicedQuantity"),
        unit_code=quantity.attrib.get("unitCode") if quantity is not None else None,
        line_extension_amount=_decimal(line_amount, "LineExtensionAmount"),
        tax_percent=_decimal(
            _first(tax_category, "Percent") if tax_category is not None else None,
            "TaxCategory/Percent",
        ),
        item_code=item_code,
    )


def parse_ubl_invoice(content: bytes) -> InvoiceData:
    if not content:
        raise UBLParseError("UBL içeriği boş olamaz.")
    if b"<!doctype" in content.lower() or b"<!entity" in content.lower():
        raise UBLParseError("DTD veya harici entity içeren XML kabul edilmez.")
    try:
        root = SafeElementTree.fromstring(content, forbid_dtd=True)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise UBLParseError("Geçersiz XML belgesi.") from exc
    if _local_name(root.tag) != INVOICE_ROOT:
        raise UBLParseError("XML kökü UBL Invoice olmalıdır.")

    monetary_total = _first(root, "LegalMonetaryTotal")
    tax_total = _first(root, "TaxTotal", "TaxAmount")
    lines = tuple(_line(line) for line in _children(root, "InvoiceLine"))
    return InvoiceData(
        invoice_id=_required_text(root, "ID"),
        issue_date=_required_text(root, "IssueDate"),
        invoice_type_code=_text(_first(root, "InvoiceTypeCode")),
        profile_id=_text(_first(root, "ProfileID")),
        currency=_text(_first(root, "DocumentCurrencyCode")),
        supplier=_party(root, "AccountingSupplierParty"),
        customer=_party(root, "AccountingCustomerParty"),
        tax_exclusive_amount=_decimal(
            _first(monetary_total, "TaxExclusiveAmount")
            if monetary_total is not None
            else None,
            "LegalMonetaryTotal/TaxExclusiveAmount",
        ),
        tax_inclusive_amount=_decimal(
            _first(monetary_total, "TaxInclusiveAmount")
            if monetary_total is not None
            else None,
            "LegalMonetaryTotal/TaxInclusiveAmount",
        ),
        payable_amount=_decimal(
            _first(monetary_total, "PayableAmount") if monetary_total is not None else None,
            "LegalMonetaryTotal/PayableAmount",
        ),
        tax_total=_decimal(tax_total, "TaxTotal/TaxAmount"),
        lines=lines,
    )
