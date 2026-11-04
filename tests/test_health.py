from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.chart.import_mizan import (
    import_mizan_workbook,
    parse_mizan_rows,
    parse_turkish_decimal,
    validate_mizan_balance,
)
from app.nace.importer import import_nace_csv, import_nace_workbook, validate_nace_codes


@pytest.mark.anyio
async def test_health() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "MaliAsistan"}


def test_turkish_mizan_import_is_balanced() -> None:
    assert parse_turkish_decimal("1.778.114.927,51") == Decimal("1778114927.51")
    rows = parse_mizan_rows(
        [
            {"kod": "100.01.001", "hesap": "Kasa", "borc": "1.000,00", "alacak": ""},
            {"kod": "600.01.001", "hesap": "Satış", "borc": "", "alacak": "1.000,00"},
        ]
    )
    assert validate_mizan_balance(rows) == Decimal("0.00")


def test_nace_import_preserves_leading_zero() -> None:
    from io import StringIO

    records = import_nace_csv(StringIO("kod,tanim\n02.40.01,Orman işletmeciliği\n"))
    validate_nace_codes(records)
    assert records[0].kod == "02.40.01"


def test_bank_api_routes_are_registered() -> None:
    paths = set(app.openapi()["paths"])

    assert "/bank/accounts" in paths
    assert "/bank/transactions" in paths
    assert "/bank/import/csv" in paths
    assert "/bank/import/xlsx" in paths
    assert "/bank/import/mt940" in paths
    assert "/bank/reconciliation/queue" in paths
    assert "/bank/reconciliation/summary" in paths
    assert "/bank/pos/aging" in paths
    assert "/erp/exports" in paths
    assert "/erp/exports/{export_id}/retry" in paths
    assert "/tax/summaries" in paths
    assert "/tax/parameters/{key}" in paths
    assert "/tax/parameters" in paths
    assert "/learning/feedback" in paths
    assert "/learning/suggestions" in paths
    assert "/bank/transactions/{transaction_id}/candidates" in paths
    assert "/bank/transactions/{transaction_id}/reconcile" in paths
    assert "/bank/transactions/{transaction_id}/ignore" in paths


@pytest.mark.anyio
async def test_dashboard_is_available() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/app")

    assert response.status_code == 200
    assert "Onay kuyruğu" in response.text


@pytest.mark.anyio
async def test_dashboard_static_assets_are_available() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        css = await client.get("/static/dashboard.css")
        javascript = await client.get("/static/dashboard.js")

    assert css.status_code == 200
    assert "--navy" in css.text
    assert javascript.status_code == 200
    assert "loadRealDocuments" in javascript.text
    assert "uploadSelectedFile" in javascript.text
    assert "25*1024*1024" in javascript.text


def test_attached_workbooks_are_importable() -> None:
    from pathlib import Path

    seed = Path(__file__).parents[1] / "data" / "seed"
    nace_files = list(seed.glob("*Nace*.xlsx"))
    mizan_files = list(seed.glob("mizan*.xlsx"))
    if not nace_files or not mizan_files:
        pytest.skip("Customer workbooks are not present in this checkout")

    related, current = import_nace_workbook(nace_files[0])
    mizan_rows, difference = import_mizan_workbook(mizan_files[0])

    assert len(related) == 1534
    assert len(current) == 2140
    assert all(record.kaynak == "sektor_meslek" for record in related)
    assert all(record.kaynak == "guncel" for record in current)
    assert any(record.kod == "02.40.01" for record in related)
    assert len(mizan_rows) == 703
    assert difference == Decimal("0.00")
