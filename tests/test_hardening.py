"""Faz 1 güvenlik sağlamlaştırma testleri."""

import io
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.audit import AuditImmutableError
from app.core.models import AuditLog
from app.core.safety import (
    UnsafeArchiveError,
    check_zip_archive,
    content_disposition,
    safe_csv_cell,
    stable_number,
)
from app.documents import router as documents_router
from app.documents.service import validate_document_content
from app.documents.storage import LocalStorage, StorageKeyError
from app.documents.ubl import UBLParseError, parse_ubl_invoice
from app.erp.universal_csv import UniversalCSVERPExportAdapter
from tests.auth_helpers import TEST_PASSWORD, make_actor, make_buro

# --- Güvenlik başlıkları ----------------------------------------------------


@pytest.mark.parametrize("path", ["/health", "/app", "/auth/me"])
async def test_security_headers_on_every_response(api_client: AsyncClient, path: str) -> None:
    res = await api_client.get(path)
    csp = res.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "frame-ancestors 'none'" in csp
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["cache-control"] == "no-store"


async def test_dashboard_has_no_inline_handlers(api_client: AsyncClient) -> None:
    html = (await api_client.get("/app")).text
    js = (await api_client.get("/static/dashboard.js")).text
    for marker in ("onclick=", "onsubmit=", "onchange="):
        assert marker not in html
        assert marker not in js


# --- Belge indirme ----------------------------------------------------------


@pytest.fixture
def memory_storage(monkeypatch, tmp_path: Path) -> LocalStorage:
    storage = LocalStorage(root=tmp_path)
    monkeypatch.setattr(documents_router, "storage", storage)
    monkeypatch.setattr(documents_router, "enqueue_extraction", lambda *a, **k: None)
    return storage


async def test_uploaded_html_disguised_as_pdf_is_never_served_as_html(
    api_client: AsyncClient, db_factory, memory_storage
) -> None:
    m = uuid4()
    buro = await make_buro(db_factory, mukellef_ids=[m])
    actor = await make_actor(db_factory, buro, rol="personel", assigned=[m])
    evil_name = 'fatura".pdf'
    res = await api_client.post(
        "/documents/upload",
        headers=actor.headers(m),
        files={"upload": (evil_name, b"%PDF-<script>alert(1)</script>", "text/html")},
    )
    assert res.status_code == 201, res.text
    doc_id = res.json()["id"]

    content = await api_client.get(f"/documents/{doc_id}/content", headers=actor.headers(m))
    assert content.status_code == 200
    assert content.headers["content-type"] == "application/pdf"
    assert content.headers["content-security-policy"].startswith("sandbox")
    assert content.headers["x-content-type-options"] == "nosniff"
    disposition = content.headers["content-disposition"]
    assert disposition.startswith("inline; ")
    # Dosya adı başlıktan kaçamaz: yalnızca filename="..." tırnak çifti kalır.
    assert disposition.count('"') == 2
    assert "\r" not in disposition and "\n" not in disposition


async def test_spreadsheet_is_downloaded_not_rendered(
    api_client: AsyncClient, db_factory, memory_storage
) -> None:
    m = uuid4()
    buro = await make_buro(db_factory, mukellef_ids=[m])
    actor = await make_actor(db_factory, buro, rol="personel", assigned=[m])
    res = await api_client.post(
        "/documents/upload",
        headers=actor.headers(m),
        files={"upload": ("liste.csv", b"<html><script>alert(1)</script>", "text/html")},
    )
    assert res.status_code == 201, res.text
    content = await api_client.get(
        f"/documents/{res.json()['id']}/content", headers=actor.headers(m)
    )
    assert content.headers["content-type"] == "application/octet-stream"
    assert content.headers["content-disposition"].startswith("attachment; ")


async def test_upload_multi_file_count_limit(
    api_client: AsyncClient, db_factory, memory_storage
) -> None:
    m = uuid4()
    buro = await make_buro(db_factory, mukellef_ids=[m])
    actor = await make_actor(db_factory, buro, rol="personel", assigned=[m])
    files = [("uploads", (f"b{i}.pdf", b"%PDF-" + bytes([i]), "application/pdf")) for i in range(21)]
    res = await api_client.post("/documents/upload-multi", headers=actor.headers(m), files=files)
    assert res.status_code == 413


def test_content_disposition_blocks_header_injection() -> None:
    value = content_disposition('a"\r\nSet-Cookie: x=1.pdf', inline=False)
    assert "\r" not in value and "\n" not in value
    assert value.startswith("attachment; filename=\"")
    assert value.count('"') == 2


# --- Depolama ve arşivler ---------------------------------------------------


async def test_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path / "store")
    for key in ("../disari.txt", "a/../../disari.txt", str(tmp_path / "mutlak.txt")):
        with pytest.raises(StorageKeyError):
            await storage.put(key, b"x")
    await storage.put("buro/muk/belge.pdf", b"ok")
    assert await storage.get("buro/muk/belge.pdf") == b"ok"


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def test_zip_bomb_and_traversal_are_rejected() -> None:
    check_zip_archive(_zip({"xl/workbook.xml": b"<x/>" * 100}))
    with pytest.raises(UnsafeArchiveError):
        check_zip_archive(_zip({"bomba.xml": b"0" * 20_000_000}))
    with pytest.raises(UnsafeArchiveError):
        check_zip_archive(_zip({"../../evil.txt": b"x"}))
    with pytest.raises(UnsafeArchiveError):
        check_zip_archive(b"PK-bozuk")
    with pytest.raises(ValueError):
        validate_document_content("spreadsheet", _zip({"bomba.xml": b"0" * 20_000_000}))


async def test_bank_import_size_and_zip_bomb_limits(api_client: AsyncClient, db_factory) -> None:
    m = uuid4()
    buro = await make_buro(db_factory, mukellef_ids=[m])
    actor = await make_actor(db_factory, buro, rol="personel", assigned=[m])
    big = await api_client.post(
        "/bank/import/csv?iban=TR000000000000000000000001",
        headers=actor.headers(m),
        files={"file": ("ekstre.csv", b"a" * (10 * 1024 * 1024 + 1), "text/csv")},
    )
    assert big.status_code == 413
    bomb = await api_client.post(
        "/bank/import/xlsx?iban=TR000000000000000000000001",
        headers=actor.headers(m),
        files={"file": ("ekstre.xlsx", _zip({"bomba.xml": b"0" * 20_000_000}), "application/zip")},
    )
    assert bomb.status_code == 400


# --- XML --------------------------------------------------------------------

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>
<Invoice>&lol2;</Invoice>"""
XXE = b"""<?xml version="1.0"?>
<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><Invoice>&xxe;</Invoice>"""


@pytest.mark.parametrize("payload", [BILLION_LAUGHS, XXE])
def test_malicious_xml_is_rejected(payload: bytes) -> None:
    with pytest.raises(UBLParseError):
        parse_ubl_invoice(payload)
    with pytest.raises(ValueError):
        validate_document_content("ubl_xml", payload)


# --- Dışa aktarma -----------------------------------------------------------


def test_csv_formula_injection_is_neutralized() -> None:
    assert safe_csv_cell("=HYPERLINK(\"http://kotu\")") == "'=HYPERLINK(\"http://kotu\")"
    assert safe_csv_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_csv_cell("-12,50") == "-12,50"
    assert safe_csv_cell("Normal açıklama") == "Normal açıklama"

    import json

    payload = json.dumps({
        "entry_date": "2026-09-01",
        "description": "=cmd|' /C calc'!A0",
        "lines": [{"account_code": "100", "account_name": "+Kasa", "debit": "10", "credit": "0"}],
    }).encode()
    csv_text = UniversalCSVERPExportAdapter().build_csv(payload)
    assert ";'=cmd" in csv_text
    assert ";'+Kasa;" in csv_text


def test_stable_number_is_deterministic() -> None:
    assert stable_number("abc", 1000) == stable_number("abc", 1000)
    assert 0 <= stable_number("xyz", 90000) < 90000


# --- Denetim kaydı ----------------------------------------------------------


async def test_audit_log_records_security_events(api_client: AsyncClient, db_factory) -> None:
    buro = await make_buro(db_factory)
    other_buro = await make_buro(db_factory)
    admin = await make_actor(db_factory, buro, rol="admin", email="adm@test.local")
    staff = await make_actor(db_factory, buro, rol="personel")
    outsider = await make_actor(db_factory, other_buro, rol="admin")

    await api_client.post("/auth/login", json={"email": "adm@test.local", "password": "Yanlis2026xx"})
    await api_client.post("/auth/login", json={"email": "adm@test.local", "password": TEST_PASSWORD})
    await api_client.post(
        "/auth/users", headers=admin.headers(),
        json={"email": "yeni@test.local", "ad_soyad": "Yeni", "password": "YeniSifre2026x"},
    )

    log = await api_client.get("/auth/audit-log", headers=admin.headers())
    assert log.status_code == 200
    actions = [(row["action"], row["outcome"]) for row in log.json()]
    assert ("auth.login", "success") in actions
    assert ("user.create", "success") in actions

    # Başarısız giriş (kullanıcı bilinmeden önce) büro kaydına değil, genel kayda düşer.
    async with db_factory() as session:
        failures = (await session.scalars(
            select(AuditLog).where(AuditLog.action == "auth.login", AuditLog.outcome == "failure")
        )).all()
        assert len(failures) == 1

    assert (await api_client.get("/auth/audit-log", headers=staff.headers())).status_code == 403
    assert (await api_client.get("/auth/audit-log", headers=outsider.headers())).json() == []


async def test_audit_log_is_append_only(db_factory) -> None:
    async with db_factory() as session:
        row = AuditLog(action="test.event")
        session.add(row)
        await session.commit()
        row.action = "degistirildi"
        with pytest.raises(AuditImmutableError):
            await session.commit()
        await session.rollback()
        with pytest.raises(AuditImmutableError):
            await session.delete(row)
            await session.commit()


# --- Giriş: IP bazlı sınır --------------------------------------------------


async def test_password_spraying_from_single_ip_is_limited(api_client: AsyncClient) -> None:
    statuses = []
    for i in range(32):
        res = await api_client.post(
            "/auth/login", json={"email": f"kisi{i}@test.local", "password": "Yanlis2026xx"}
        )
        statuses.append(res.status_code)
    assert 429 in statuses
    assert statuses.index(429) == 30
