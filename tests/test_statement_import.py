"""Evrensel ekstre okuyucu ve içe aktarma (gerçek banka düzenlerini taklit eden sentetik veri)."""

import io
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from openpyxl import Workbook

from app.bank import statement_parser
from app.bank.statement_parser import StatementParseError, parse_amount, parse_statement
from app.documents import router as documents_router
from app.documents.storage import LocalStorage
from tests.auth_helpers import make_actor, make_buro

IBAN = "TR140001500158007311632419"


def _xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def vakif_like(bad_balance: bool = False) -> bytes:
    """Üstte hesap bilgisi bloğu, 7. satırda başlık, eskiden yeniye sıralı."""
    rows = [
        ["HESAP SAHİBİ", "ÖRNEK LTD", None, "TARİH VE SAAT", "14.10.2026 13:35"],
        ["VB MÜŞTERİ NO", "123", None, "BAKİYE", "23.605,20 TL"],
        ["IBAN", "TR14 0001 5001 5800 7311 6324 19", None, None, None],
        ["HESAP TÜRÜ", "VADESİZ TL", None, None, None],
        [None, None, None, None, None],
        ["HESAP NO", "FİŞ NO", "HAREKET TARIH", "İŞLEM TARİHİ", "İŞLEM", "TUTAR", "BAKİYE", "İŞLEM NO", "AÇIKLAMA"],
        ["001", 0, "01.07.2026 09:36", "01.07.2026", "FAST Anlık Ödeme", -5000, 326506.79, "A1", "AVANS ÖDEMESİ"],
        ["001", 0, "01.07.2026 10:16", "01.07.2026", "Gelen FAST", 20000, 346506.79, "A2", "MÜŞTERİ TAHSİLATI"],
        ["001", 0, "02.07.2026 13:30", "02.07.2026", "Gelen EFT", 15000, 99999.99 if bad_balance else 361506.79, "A3", "EFT"],
    ]
    return _xlsx(rows)


def ziraat_like() -> bytes:
    """Başlık ilk satırda, IBAN yok, yeniden eskiye sıralı, işaretli tutar."""
    return _xlsx([
        ["Tarih", "Fiş No", "Açıklama", "İşlem Tutarı", "Bakiye"],
        ["30.09.2026", "F04116", "KK TAHSİLAT", -60224.1, 3388.84],
        ["30.09.2026", "F04114", "KOMİSYON BSMV", -13.77, 63612.94],
        ["29.09.2026", "F04110", "GELEN HAVALE", 1000, 63626.71],
    ])


YAPI_KREDI_TEXT = """Hesap Hareketleri
IBAN/Hesap No : TR460006701000000035524453
Tarih Saat İşlem Kanal Açıklama İşlem Tutarı Bakiye
30/09/2026 11:19:03 Diğer Diğer Faiz Tahsilat BSMV , 35524453 YTL -0,12 TL 497,44 TL
30/09/2026 09:34:22 Para Gönder Diğer
GELEN FAST - ÖRNEK YAPILAR
İNŞAAT - şirket hesapları havale 500,00 TL 497,56 TL
16/09/2026 12:20:32 Para Gönder Internet - Mobil
GİDEN EFT - Örnek - Şirket
hesapları arası havale -354.000,00 TL -2,44 TL
Yapı ve Kredi Bankası A.Ş. 1/4
16/09/2026 09:50:40 Diğer Diğer GELEN FAST 353.997,56 TL 353.997,56 TL
"""


# --- Okuyucu ----------------------------------------------------------------


def test_turkish_amounts() -> None:
    assert parse_amount("1.234,56") == Decimal("1234.56")
    assert parse_amount("-354.000,00 TL") == Decimal("-354000.00")
    assert parse_amount("(12,50)") == Decimal("-12.50")
    assert parse_amount(-60224.1) == Decimal("-60224.10")
    assert parse_amount("1,234.56") == Decimal("1234.56")
    assert parse_amount("abc") is None


def test_vakif_layout_header_block_and_iban() -> None:
    s = parse_statement(vakif_like(), "VAKIFBANK.xlsx")
    assert s.iban == IBAN
    assert s.bank_name == "VakıfBank"
    assert [l.amount for l in s.lines] == [Decimal("-5000.00"), Decimal("20000.00"), Decimal("15000.00")]
    assert s.lines[0].description.startswith("FAST Anlık Ödeme - AVANS")
    assert s.lines[0].time == "09:36"
    assert s.balance_checked == 2 and not s.balance_mismatches


def test_balance_chain_catches_wrong_rows() -> None:
    s = parse_statement(vakif_like(bad_balance=True), "VAKIFBANK.xlsx")
    assert len(s.balance_mismatches) == 1
    assert s.warnings


def test_ziraat_layout_reverse_order_without_iban() -> None:
    s = parse_statement(ziraat_like(), "ZİRAAT BANKASI.xlsx")
    assert s.iban is None
    assert s.bank_name == "Ziraat Bankası"
    # Bakiye zincirine göre eskiden yeniye çevrildi
    assert [l.amount for l in s.lines] == [Decimal("1000.00"), Decimal("-13.77"), Decimal("-60224.10")]
    assert s.balance_checked == 2 and not s.balance_mismatches


def test_csv_with_debit_credit_columns() -> None:
    csv_text = (
        "Banka: Akbank\nIBAN: TR46 0006 7010 0000 0035 5244 53\n\n"
        "İşlem Tarihi;Açıklama;Borç;Alacak;Bakiye\n"
        "01.08.2026;Kira ödemesi;10.000,00;;5.000,00\n"
        "02.08.2026;Tahsilat;;2.500,00;7.500,00\n"
        "TOPLAM;;10.000,00;2.500,00;\n"
    )
    s = parse_statement(csv_text.encode("utf-8"), "ekstre.csv")
    assert s.iban == "TR460006701000000035524453"
    assert [l.amount for l in s.lines] == [Decimal("-10000.00"), Decimal("2500.00")]
    assert s.balance_checked == 1 and not s.balance_mismatches


def test_pdf_multiline_transactions(monkeypatch) -> None:
    class Fake:
        text = YAPI_KREDI_TEXT

    monkeypatch.setattr(statement_parser, "extract_pdf_text", lambda content: Fake())
    s = parse_statement(b"%PDF-fake", "YAPI KREDİ.pdf")
    assert s.iban == "TR460006701000000035524453"
    assert s.bank_name == "Yapı Kredi"
    assert len(s.lines) == 4
    assert s.balance_checked == 3 and not s.balance_mismatches, s.balance_mismatches
    eft = next(l for l in s.lines if l.amount == Decimal("-354000.00"))
    assert "GİDEN EFT" in eft.description and "hesapları arası havale" in eft.description


def test_stable_ids_and_unsupported_inputs() -> None:
    a = parse_statement(vakif_like(), "v.xlsx")
    b = parse_statement(vakif_like(), "v.xlsx")
    assert [l.external_id for l in a.lines] == [l.external_id for l in b.lines]
    assert len({l.external_id for l in a.lines}) == 3
    with pytest.raises(StatementParseError, match="OCR"):
        parse_statement(b"\xff\xd8\xff" + b"0" * 10, "foto.jpg")
    with pytest.raises(StatementParseError):
        parse_statement(b"sadece metin\nbaska satir", "x.csv")


# --- API --------------------------------------------------------------------


@pytest.fixture
async def office(db_factory):
    m = uuid4()
    buro = await make_buro(db_factory, mukellef_ids=[m])
    return {
        "m": m,
        "personel": await make_actor(db_factory, buro, rol="personel", assigned=[m]),
        "mukellef": await make_actor(db_factory, buro, rol="mukellef", assigned=[m]),
        "admin": await make_actor(db_factory, buro, rol="admin"),
    }


async def test_import_endpoint_creates_account_and_dedupes(api_client: AsyncClient, office) -> None:
    h = office["personel"].headers(office["m"])
    files = {"file": ("VAKIFBANK.xlsx", vakif_like(), "application/octet-stream")}
    first = await api_client.post("/bank/import", headers=h, files=files)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] == 3 and body["skipped"] == 0
    assert body["account_created"] is True and body["ledger_account_code"] == "102.01"
    assert body["balance_ok"] == body["balance_checked"] == 2

    again = await api_client.post("/bank/import", headers=h, files=files)
    assert again.json()["created"] == 0 and again.json()["skipped"] == 3

    accounts = (await api_client.get("/bank/accounts", headers=h)).json()["accounts"]
    assert [a["iban"] for a in accounts] == [IBAN]
    txs = (await api_client.get("/bank/transactions", headers=h)).json()["transactions"]
    assert len(txs) == 3
    assert {t["debit"] for t in txs if Decimal(t["debit"]) > 0} == {"20000.00", "15000.00"}

    log = (await api_client.get("/auth/audit-log", headers=office["admin"].headers())).json()
    assert any(row["action"] == "bank.import" for row in log)


async def test_import_without_iban_asks_for_it(api_client: AsyncClient, office) -> None:
    h = office["personel"].headers(office["m"])
    files = {"file": ("ZİRAAT BANKASI.xlsx", ziraat_like(), "application/octet-stream")}
    res = await api_client.post("/bank/import", headers=h, files=files)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "iban_required"
    assert res.json()["detail"]["transactions"] == 3

    ok = await api_client.post(
        "/bank/import", headers=h, files=files, params={"iban": "TR33 0001 0000 0000 0000 0000 01"}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["created"] == 3 and ok.json()["bank_name"] == "Ziraat Bankası"


async def test_import_rejects_iban_conflict_and_readonly_role(api_client: AsyncClient, office) -> None:
    files = {"file": ("VAKIFBANK.xlsx", vakif_like(), "application/octet-stream")}
    conflict = await api_client.post(
        "/bank/import", headers=office["personel"].headers(office["m"]), files=files,
        params={"iban": "TR330001000000000000000001"},
    )
    assert conflict.status_code == 422 and "uyuşmuyor" in conflict.json()["detail"]
    readonly = await api_client.post(
        "/bank/import", headers=office["mukellef"].headers(office["m"]), files=files
    )
    assert readonly.status_code == 403


async def test_statement_uploaded_as_document_goes_to_bank(
    api_client: AsyncClient, office, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(documents_router, "storage", LocalStorage(root=tmp_path))
    monkeypatch.setattr(documents_router, "enqueue_extraction", lambda *a, **k: None)
    h = office["personel"].headers(office["m"])
    up = await api_client.post(
        "/documents/upload", headers=h,
        files={"upload": ("VAKIFBANK.xlsx", vakif_like(),
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up.status_code == 201, up.text
    doc_id = up.json()["id"]
    await api_client.post(f"/documents/{doc_id}/extract", headers=h)

    detail = (await api_client.get(f"/documents/{doc_id}", headers=h)).json()
    assert detail["document_category"] == "bank_statement"
    assert detail["extraction_metadata"]["posting"]["status"] == "bank_imported"
    assert detail["review_status"] == "approved"
    txs = (await api_client.get("/bank/transactions", headers=h)).json()["transactions"]
    assert len(txs) == 3
