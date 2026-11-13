"""Faz 2: belge → TDHP taslak yevmiye → inceleme kuyruğu → onay (uçtan uca)."""

from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.documents import router as documents_router
from app.documents.storage import LocalStorage
from app.documents.ubl import parse_ubl_invoice
from tests.auth_helpers import make_actor, make_buro

MUKELLEF_VKN = "1111111111"
SUPPLIER_VKN = "2222222222"
CUSTOMER_VKN = "3333333333"


def ubl(
    *,
    invoice_id: str,
    supplier_vkn: str,
    customer_vkn: str,
    base: str = "1000.00",
    vat: str = "200.00",
    payable: str = "1200.00",
    type_code: str = "SATIS",
    customer_person: bool = False,
) -> bytes:
    customer_name = (
        "<cac:Person><cbc:FirstName>Ayşe</cbc:FirstName><cbc:FamilyName>Yılmaz</cbc:FamilyName></cac:Person>"
        if customer_person
        else "<cac:PartyName><cbc:Name>ALICI A.Ş.</cbc:Name></cac:PartyName>"
    )
    scheme = "TCKN" if customer_person else "VKN"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ProfileID>TICARIFATURA</cbc:ProfileID>
 <cbc:ID>{invoice_id}</cbc:ID>
 <cbc:IssueDate>2026-09-15</cbc:IssueDate>
 <cbc:InvoiceTypeCode>{type_code}</cbc:InvoiceTypeCode>
 <cbc:DocumentCurrencyCode>TRY</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party>
  <cac:PartyIdentification><cbc:ID schemeID="VKN">{supplier_vkn}</cbc:ID></cac:PartyIdentification>
  <cac:PartyIdentification><cbc:ID schemeID="MERSISNO">0000</cbc:ID></cac:PartyIdentification>
  <cac:PartyName><cbc:Name>SATICI LTD.</cbc:Name></cac:PartyName>
 </cac:Party></cac:AccountingSupplierParty>
 <cac:AccountingCustomerParty><cac:Party>
  <cac:PartyIdentification><cbc:ID schemeID="{scheme}">{customer_vkn}</cbc:ID></cac:PartyIdentification>
  {customer_name}
 </cac:Party></cac:AccountingCustomerParty>
 <cac:TaxTotal><cbc:TaxAmount currencyID="TRY">{vat}</cbc:TaxAmount></cac:TaxTotal>
 <cac:LegalMonetaryTotal>
  <cbc:TaxExclusiveAmount currencyID="TRY">{base}</cbc:TaxExclusiveAmount>
  <cbc:TaxInclusiveAmount currencyID="TRY">{payable}</cbc:TaxInclusiveAmount>
  <cbc:PayableAmount currencyID="TRY">{payable}</cbc:PayableAmount>
 </cac:LegalMonetaryTotal>
 <cac:InvoiceLine><cbc:ID>1</cbc:ID>
  <cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity>
  <cbc:LineExtensionAmount currencyID="TRY">{base}</cbc:LineExtensionAmount>
  <cac:Item><cbc:Name>Malzeme</cbc:Name></cac:Item>
 </cac:InvoiceLine>
</Invoice>""".encode()


# --- Adım 1: UBL VKN/TCKN ---------------------------------------------------


def test_ubl_reads_vkn_from_party_identification_and_person_name() -> None:
    invoice = parse_ubl_invoice(
        ubl(invoice_id="A1", supplier_vkn=SUPPLIER_VKN, customer_vkn="12345678901",
            customer_person=True)
    )
    assert invoice.supplier.tax_id == SUPPLIER_VKN
    assert invoice.supplier.tax_id_scheme == "VKN"
    assert invoice.customer.tax_id == "12345678901"
    assert invoice.customer.tax_id_scheme == "TCKN"
    assert invoice.customer.name == "Ayşe Yılmaz"


# --- Uçtan uca --------------------------------------------------------------


@pytest.fixture
async def office(db_factory, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(documents_router, "storage", LocalStorage(root=tmp_path))
    monkeypatch.setattr(documents_router, "enqueue_extraction", lambda *a, **k: None)
    m = uuid4()
    buro = await make_buro(db_factory, mukellef_ids=[m])
    async with db_factory() as session:
        from app.core.models import Mukellef

        mukellef = await session.get(Mukellef, m)
        mukellef.vkn_tckn = MUKELLEF_VKN
        await session.commit()
    return {
        "m": m,
        "admin": await make_actor(db_factory, buro, rol="admin"),
        "musavir": await make_actor(db_factory, buro, rol="musavir"),
        "personel": await make_actor(db_factory, buro, rol="personel", assigned=[m]),
    }


async def _upload_and_process(client: AsyncClient, office, content: bytes, name: str) -> str:
    headers = office["personel"].headers(office["m"])
    res = await client.post(
        "/documents/upload", headers=headers, files={"upload": (name, content, "application/xml")}
    )
    assert res.status_code == 201, res.text
    doc_id = res.json()["id"]
    processed = await client.post(f"/documents/{doc_id}/extract", headers=headers)
    assert processed.status_code == 200, processed.text
    return doc_id


async def _queue_item(client: AsyncClient, office, doc_id: str, **params) -> dict:
    res = await client.get(
        "/documents/queue", headers=office["personel"].headers(office["m"]), params=params
    )
    assert res.status_code == 200
    return next((i for i in res.json()["items"] if i["id"] == doc_id), None)


def _lines(item: dict) -> list[tuple[str, str, str]]:
    return [(l["account_code"], l["debit"], l["credit"]) for l in item["entry"]["lines"]]


async def test_purchase_invoice_flow_end_to_end(api_client: AsyncClient, office) -> None:
    doc_id = await _upload_and_process(
        api_client, office,
        ubl(invoice_id="ALS001", supplier_vkn=SUPPLIER_VKN, customer_vkn=MUKELLEF_VKN),
        "alis.xml",
    )
    item = await _queue_item(api_client, office, doc_id)
    assert item["posting"]["direction"] == "purchase"
    # Öğrenilmiş hesap yok: varsayılan 153, güven eşiğin altında -> inceleme gerekli
    assert item["posting"]["status"] == "needs_review"
    assert item["review_status"] == "needs_review"
    assert _lines(item) == [
        ("153", "1000.00", "0.00"),
        ("191", "200.00", "0.00"),
        ("320.01.001", "0.00", "1200.00"),
    ]
    assert item["invoice"]["supplier"]["tax_id"] == SUPPLIER_VKN

    # Personel onaylayamaz
    p_headers = office["personel"].headers(office["m"])
    assert (await api_client.post(f"/documents/{doc_id}/approve", headers=p_headers)).status_code == 403

    # Personel hesabı 770'e düzeltir
    corrected = await api_client.post(
        f"/documents/{doc_id}/correct", headers=p_headers,
        json={"account_code": "770.01", "account_name": "Genel Yönetim Giderleri"},
    )
    assert corrected.status_code == 200, corrected.text
    assert _lines(corrected.json())[0] == ("770.01", "1000.00", "0.00")
    assert corrected.json()["posting"]["status"] == "draft_ready"

    # Müşavir onaylar -> fiş kesinleşir, kuyruktan çıkar, mizana yansır
    m_headers = office["musavir"].headers(office["m"])
    approved = await api_client.post(f"/documents/{doc_id}/approve", headers=m_headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["entry"]["status"] == "approved"
    assert await _queue_item(api_client, office, doc_id) is None
    assert (await api_client.post(f"/documents/{doc_id}/approve", headers=m_headers)).status_code == 409

    entries = (await api_client.get("/ledger/entries", headers=m_headers)).json()["entries"]
    assert len(entries) == 1 and entries[0]["status"] == "approved"
    tb = (await api_client.get("/ledger/trial-balance", headers=m_headers)).json()
    assert tb["is_balanced"] is True

    log = (await api_client.get("/auth/audit-log", headers=office["admin"].headers())).json()
    actions = {row["action"] for row in log}
    assert {"document.upload", "document.correct", "document.approve"} <= actions


async def test_sales_invoice_is_ready_for_approval(api_client: AsyncClient, office) -> None:
    doc_id = await _upload_and_process(
        api_client, office,
        ubl(invoice_id="SAT001", supplier_vkn=MUKELLEF_VKN, customer_vkn=CUSTOMER_VKN),
        "satis.xml",
    )
    item = await _queue_item(api_client, office, doc_id)
    assert item["posting"]["direction"] == "sales"
    assert item["posting"]["status"] == "draft_ready"
    assert _lines(item) == [
        ("120.01.001", "1200.00", "0.00"),
        ("600", "0.00", "1000.00"),
        ("391", "0.00", "200.00"),
    ]


async def test_foreign_and_unbalanced_invoices_go_to_review(api_client: AsyncClient, office) -> None:
    foreign = await _upload_and_process(
        api_client, office,
        ubl(invoice_id="YAB001", supplier_vkn=SUPPLIER_VKN, customer_vkn=CUSTOMER_VKN),
        "yabanci.xml",
    )
    item = await _queue_item(api_client, office, foreign)
    assert item["entry"] is None and item["review_status"] == "needs_review"
    assert "mükellefe ait" in item["posting"]["reasons"][0]

    unbalanced = await _upload_and_process(
        api_client, office,
        ubl(invoice_id="OTV001", supplier_vkn=SUPPLIER_VKN, customer_vkn=MUKELLEF_VKN,
            base="361.53", vat="162.43", payable="662.00"),
        "otv.xml",
    )
    item = await _queue_item(api_client, office, unbalanced)
    assert item["entry"] is None
    assert any("dengeli değil" in r for r in item["posting"]["reasons"])

    withholding = await _upload_and_process(
        api_client, office,
        ubl(invoice_id="TEV001", supplier_vkn=SUPPLIER_VKN, customer_vkn=MUKELLEF_VKN,
            type_code="TEVKIFAT"),
        "tevkifat.xml",
    )
    item = await _queue_item(api_client, office, withholding)
    assert item["entry"] is None and "TEVKIFAT" in item["posting"]["reasons"][0]


async def test_later_keeps_document_in_queue(api_client: AsyncClient, office) -> None:
    doc_id = await _upload_and_process(
        api_client, office,
        ubl(invoice_id="SAT002", supplier_vkn=MUKELLEF_VKN, customer_vkn=CUSTOMER_VKN),
        "satis2.xml",
    )
    res = await api_client.post(
        f"/documents/{doc_id}/later", headers=office["personel"].headers(office["m"])
    )
    assert res.status_code == 200
    assert res.json()["review_status"] == "needs_review"
    assert res.json()["entry"]["status"] == "draft"


async def test_system_learns_account_after_repeated_approvals(
    api_client: AsyncClient, office
) -> None:
    m_headers = office["musavir"].headers(office["m"])
    for i in range(3):
        doc_id = await _upload_and_process(
            api_client, office,
            ubl(invoice_id=f"OGR{i}", supplier_vkn=SUPPLIER_VKN, customer_vkn=MUKELLEF_VKN,
                base=f"{100 + i}.00", vat="20.00", payable=f"{120 + i}.00"),
            f"ogr{i}.xml",
        )
        await api_client.post(
            f"/documents/{doc_id}/correct", headers=m_headers,
            json={"account_code": "770.01", "account_name": "Genel Yönetim Giderleri"},
        )
        assert (await api_client.post(f"/documents/{doc_id}/approve", headers=m_headers)).status_code == 200

    new_doc = await _upload_and_process(
        api_client, office,
        ubl(invoice_id="OGR9", supplier_vkn=SUPPLIER_VKN, customer_vkn=MUKELLEF_VKN),
        "ogr9.xml",
    )
    item = await _queue_item(api_client, office, new_doc)
    assert _lines(item)[0][0] == "770.01"
    assert item["posting"]["status"] == "draft_ready"
    assert "daha önce onaylanan" in item["posting"]["reasons"][0]


async def test_unreadable_document_shows_in_queue_for_manual_entry(
    api_client: AsyncClient, office
) -> None:
    headers = office["personel"].headers(office["m"])
    res = await api_client.post(
        "/documents/upload", headers=headers,
        files={"upload": ("fis.jpg", b"\xff\xd8\xff" + b"0" * 50, "image/jpeg")},
    )
    doc_id = res.json()["id"]
    await api_client.post(f"/documents/{doc_id}/extract", headers=headers)
    item = await _queue_item(api_client, office, doc_id)
    assert item is not None
    assert item["review_status"] == "needs_review"
    assert "elle giriş" in item["posting"]["reasons"][0]


async def test_reversal_is_approved_once_and_nets_trial_balance(
    api_client: AsyncClient, office
) -> None:
    doc_id = await _upload_and_process(
        api_client, office,
        ubl(invoice_id="TRS001", supplier_vkn=MUKELLEF_VKN, customer_vkn=CUSTOMER_VKN),
        "ters.xml",
    )
    m_headers = office["musavir"].headers(office["m"])
    approved = (await api_client.post(f"/documents/{doc_id}/approve", headers=m_headers)).json()
    entry_id = approved["entry"]["id"]

    first = await api_client.post(f"/ledger/entries/{entry_id}/reverse", headers=m_headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "approved"

    tb = (await api_client.get("/ledger/trial-balance", headers=m_headers)).json()
    assert tb["is_balanced"] is True
    assert all(row["balance"] in ("0.00", "0") for row in tb["trial_balance"]), tb

    again = await api_client.post(f"/ledger/entries/{entry_id}/reverse", headers=m_headers)
    assert again.status_code == 400 and "zaten" in again.json()["detail"]
    reversal_id = first.json()["reversal_entry_id"]
    nested = await api_client.post(f"/ledger/entries/{reversal_id}/reverse", headers=m_headers)
    assert nested.status_code == 400
