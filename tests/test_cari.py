"""Mükellefe özel cari kırılımları: ardışık kod, mizanla eşleştirme, otomatik açılış."""

import io
from uuid import uuid4

from httpx import AsyncClient
from openpyxl import Workbook

from app.chart.cari import next_code, normalize_name
from app.core.models import Mukellef
from tests.auth_helpers import make_actor, make_buro
from tests.test_posting import ubl

MUKELLEF_VKN = "1111111111"


def test_next_code_follows_existing_numbering() -> None:
    assert next_code("120", set()) == "120.01.001"
    codes = {"120", "120.01", "120.01.001", "120.01.002", "120.01.045"}
    assert next_code("120", codes) == "120.01.046"
    # Farklı şema: iki seviyeli, 4 haneli
    assert next_code("320", {"320", "320.0007", "320.0012"}) == "320.0013"
    # En kalabalık grup izlenir
    assert next_code("120", {"120.01.001", "120.02.001", "120.02.009"}) == "120.02.010"


def test_name_normalization_keeps_sector_words() -> None:
    assert normalize_name("ABC İnşaat San. ve Tic. Ltd. Şti.") == normalize_name(
        "ABC İNŞAAT SANAYİ VE TİCARET LİMİTED ŞİRKETİ"
    )
    assert normalize_name("ABC İnşaat Ltd") != normalize_name("ABC Gıda Ltd")


def _mizan() -> bytes:
    wb = Workbook()
    for row in [
        ["Hesap Kodu", "Hesap Adı", "Borç", "Alacak"],
        ["120", "ALICILAR", 300, 0],
        ["120.01", "YURTİÇİ ALICILAR", 300, 0],
        ["120.01.001", "ESKİ MÜŞTERİ A.Ş.", 100, 0],
        ["120.01.007", "ALICI A.Ş.", 200, 0],
        ["600", "YURTİÇİ SATIŞLAR", 0, 300],
    ]:
        wb.active.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


async def _office(db_factory, monkeypatch, tmp_path):
    from app.documents import router as documents_router
    from app.documents.storage import LocalStorage

    monkeypatch.setattr(documents_router, "storage", LocalStorage(root=tmp_path))
    monkeypatch.setattr(documents_router, "enqueue_extraction", lambda *a, **k: None)
    m1, m2 = uuid4(), uuid4()
    buro = await make_buro(db_factory, mukellef_ids=[m1, m2])
    async with db_factory() as session:
        for m in (m1, m2):
            (await session.get(Mukellef, m)).vkn_tckn = MUKELLEF_VKN
        await session.commit()
    actor = await make_actor(db_factory, buro, rol="musavir")
    return actor, m1, m2


async def _post(client: AsyncClient, actor, m, content: bytes, name: str) -> dict:
    h = actor.headers(m)
    doc = (await client.post("/documents/upload", headers=h,
                             files={"upload": (name, content, "application/xml")})).json()
    await client.post(f"/documents/{doc['id']}/extract", headers=h)
    queue = (await client.get("/documents/queue", headers=h)).json()["items"]
    return next(i for i in queue if i["id"] == doc["id"])


async def test_cari_auto_created_sequentially_from_mizan(
    api_client: AsyncClient, db_factory, monkeypatch, tmp_path
) -> None:
    actor, m1, m2 = await _office(db_factory, monkeypatch, tmp_path)
    h1 = actor.headers(m1)
    up = await api_client.post("/chart/mizan", headers=h1,
                               files={"file": ("mizan.xlsx", _mizan(), "application/octet-stream")})
    assert up.status_code == 200, up.text

    # Mizanda aynı unvan var -> yeni kod açılmaz, mevcut 120.01.007 kullanılır
    known = await _post(api_client, actor, m1,
                        ubl(invoice_id="S1", supplier_vkn=MUKELLEF_VKN, customer_vkn="3333333333"), "s1.xml")
    assert known["entry"]["lines"][0]["account_code"] == "120.01.007"
    assert any("mevcut 120.01.007" in r for r in known["posting"]["reasons"])

    # Mizanda olmayan yeni müşteri -> kaldığı yerden ardışık: 120.01.008
    new_customer = ubl(invoice_id="S2", supplier_vkn=MUKELLEF_VKN, customer_vkn="4444444444")
    new_customer = new_customer.replace(b"ALICI A.\xc5\x9e.", "YENİ MÜŞTERİ LTD.".encode())
    item = await _post(api_client, actor, m1, new_customer, "s2.xml")
    assert item["entry"]["lines"][0]["account_code"] == "120.01.008"
    assert item["entry"]["lines"][0]["account_name"] == "YENİ MÜŞTERİ LTD."
    assert any("Yeni cari açıldı: 120.01.008" in r for r in item["posting"]["reasons"])

    # Aynı VKN tekrar gelirse aynı cari
    again = await _post(api_client, actor, m1,
                        ubl(invoice_id="S3", supplier_vkn=MUKELLEF_VKN, customer_vkn="4444444444"), "s3.xml")
    assert again["entry"]["lines"][0]["account_code"] == "120.01.008"

    # Başka mükellefin kendi numaralandırması: mizanı yok -> 120.01.001
    other = await _post(api_client, actor, m2,
                        ubl(invoice_id="S4", supplier_vkn=MUKELLEF_VKN, customer_vkn="4444444444"), "s4.xml")
    assert other["entry"]["lines"][0]["account_code"] == "120.01.001"

    cari = (await api_client.get("/chart/cari", headers=h1, params={"ana_hesap": "120"})).json()["cari"]
    assert [c["hesap_kodu"] for c in cari] == ["120.01.007", "120.01.008"]
    assert cari[1]["vkn_tckn"] == "4444444444" and cari[1]["otomatik"] is True


async def test_cari_balance_and_manual_create(
    api_client: AsyncClient, db_factory, monkeypatch, tmp_path
) -> None:
    actor, m1, _ = await _office(db_factory, monkeypatch, tmp_path)
    h = actor.headers(m1)
    item = await _post(api_client, actor, m1,
                       ubl(invoice_id="S9", supplier_vkn=MUKELLEF_VKN, customer_vkn="5555555555"), "s9.xml")
    await api_client.post(f"/documents/{item['id']}/approve", headers=h)
    cari = (await api_client.get("/chart/cari", headers=h)).json()["cari"]
    assert cari[0]["hesap_kodu"] == "120.01.001" and cari[0]["bakiye"] == "1200.00"

    manual = await api_client.post("/chart/cari", headers=h,
                                   json={"ana_hesap": "320", "unvan": "ELLE SATICI", "vkn_tckn": "6666666666"})
    assert manual.status_code == 201 and manual.json()["hesap_kodu"] == "320.01.001"
    dup = await api_client.post("/chart/cari", headers=h,
                                json={"ana_hesap": "320", "unvan": "AYNI VKN", "vkn_tckn": "6666666666"})
    assert dup.status_code == 409
    explicit = await api_client.post("/chart/cari", headers=h,
                                     json={"ana_hesap": "320", "unvan": "KODLU", "hesap_kodu": "320.05.010"})
    assert explicit.status_code == 201 and explicit.json()["hesap_kodu"] == "320.05.010"
    wrong = await api_client.post("/chart/cari", headers=h,
                                  json={"ana_hesap": "320", "unvan": "YANLIS ANA", "hesap_kodu": "120.01.050"})
    assert wrong.status_code == 422
