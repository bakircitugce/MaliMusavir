"""Panelden mizan yükleme (Excel / CSV, hiyerarşik hesaplar)."""

import io
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from openpyxl import Workbook

from app.chart.mizan_upload import MizanParseError, parse_mizan_file
from tests.auth_helpers import make_actor, make_buro


def _xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


HIERARCHICAL = [
    ["ÖRNEK LTD. ŞTİ.", None, None, None],
    ["MİZAN 01.01.2026 - 30.09.2026", None, None, None],
    [None, None, None, None],
    ["Hesap Kodu", "Hesap Adı", "Borç", "Alacak"],
    ["100", "KASA", 1000, 400],
    ["100.01", "MERKEZ KASA", 1000, 400],
    ["102", "BANKALAR", 5000, 0],
    ["102.01", "VAKIFBANK", 3000, 0],
    ["102.02", "YAPI KREDİ", 2000, 0],
    ["320", "SATICILAR", 0, 5600],
    ["320.01.001", "ÖRNEK SATICI", 0, 5600],
    ["TOPLAM", None, 6000, 6000],
]


def test_hierarchical_mizan_with_total_row() -> None:
    result = parse_mizan_file(_xlsx(HIERARCHICAL), "mizan.xlsx")
    assert len(result.rows) == 7
    assert result.method == "TOPLAM satırı"
    assert result.balanced
    kasa = next(r for r in result.rows if r.hesap_kodu == "100.01")
    assert kasa.hesap_adi == "MERKEZ KASA" and kasa.borc == Decimal("1000.00")


def test_mizan_without_total_uses_main_accounts() -> None:
    result = parse_mizan_file(_xlsx(HIERARCHICAL[:-1]), "mizan.xlsx")
    assert result.method == "ana hesaplar (3 haneli)"
    assert result.total_debit == Decimal("6000.00") and result.balanced


def test_csv_mizan_and_unbalanced_warning() -> None:
    text = (
        "Firma: Örnek\n\nKod;Hesap;Borç;Alacak\n"
        "100;Kasa;1.000,00;\n153;Ticari Mallar;2.500,00;\n320;Satıcılar;;3.000,00\n"
    )
    result = parse_mizan_file(text.encode("utf-8"), "mizan.csv")
    assert len(result.rows) == 3
    assert not result.balanced and result.difference == Decimal("500.00")
    assert any("dengeli değil" in w for w in result.warnings)


def test_unrecognized_file_is_rejected() -> None:
    with pytest.raises(MizanParseError):
        parse_mizan_file(_xlsx([["a", "b"], [1, 2]]), "x.xlsx")
    with pytest.raises(MizanParseError):
        parse_mizan_file(b"%PDF-1.4", "mizan.pdf")


async def test_mizan_upload_endpoint_and_summary(api_client: AsyncClient, db_factory) -> None:
    m = uuid4()
    buro = await make_buro(db_factory, mukellef_ids=[m])
    staff = await make_actor(db_factory, buro, rol="personel", assigned=[m])
    h = staff.headers(m)

    empty = (await api_client.get("/chart/summary", headers=h)).json()
    assert empty["accounts"] == 0 and empty["balanced"] is False

    files = {"file": ("mizan.xlsx", _xlsx(HIERARCHICAL), "application/octet-stream")}
    res = await api_client.post("/chart/mizan", headers=h, files=files)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["accounts"] == 7 and body["inserted"] == 7 and body["balanced"] is True

    again = (await api_client.post("/chart/mizan", headers=h, files=files)).json()
    assert again["inserted"] == 0 and again["unchanged"] == 7

    summary = (await api_client.get("/chart/summary", headers=h)).json()
    assert summary == {"accounts": 7, "total_debit": "6000.00", "total_credit": "6000.00", "balanced": True}

    bad = await api_client.post(
        "/chart/mizan", headers=h, files={"file": ("x.xlsx", _xlsx([["a"], [1]]), "application/octet-stream")}
    )
    assert bad.status_code == 422
