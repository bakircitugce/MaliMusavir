"""Faz 0 çıkış kriterleri: kimlik doğrulama, kiracı izolasyonu ve rol matrisi.

Route'lar uygulamadan otomatik toplanır; yeni eklenen bir route bu testlerden
kaçamaz. Yeni bir route herkese açık olacaksa app.main.PUBLIC_PATHS'e, kiracı
gerektirmiyorsa TENANTLESS'e açıkça eklenmelidir.
"""

import re
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.models import BankAccount, BankTransaction, Belge, JournalEntry, JournalLine
from app.main import PUBLIC_PATHS, app
from tests.auth_helpers import make_actor, make_buro

_SAFE = {"GET", "HEAD", "OPTIONS"}

# Kiracı (X-Mukellef-ID) gerektirmeyen korumalı route'lar.
TENANTLESS = {
    ("GET", "/einvoice/providers"),
}

# Yalnızca admin/müşavirin yapabileceği işlemler.
APPROVER_ONLY = {
    ("POST", "/ledger/entries/{entry_id}/reverse"),
    ("POST", "/erp/exports/{export_id}/retry"),
    ("POST", "/einvoice/submit"),
    ("POST", "/documents/{document_id}/approve"),
}


def _api_routes() -> list[tuple[str, str]]:
    routes = []
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            routes.append((method.upper(), path))
    return sorted(routes)


ALL_ROUTES = _api_routes()
PROTECTED = [r for r in ALL_ROUTES if r[1] not in PUBLIC_PATHS]
DATA_ROUTES = [r for r in PROTECTED if not r[1].startswith("/auth/")]
TENANT_ROUTES = [r for r in DATA_ROUTES if r not in TENANTLESS]


def _fill(path: str, values: dict[str, str] | None = None) -> str:
    values = values or {}
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), str(uuid4())), path)


def test_route_inventory_is_complete() -> None:
    assert len(ALL_ROUTES) > 40
    assert APPROVER_ONLY <= set(ALL_ROUTES)
    assert TENANTLESS <= set(ALL_ROUTES)
    # Herkese açık yollar bilinçli olarak dar tutulur.
    assert PUBLIC_PATHS == {"/health", "/auth/login", "/app"}


# --- 0.3: Token'sız her çağrı 401 -------------------------------------------


@pytest.mark.parametrize(("method", "path"), PROTECTED, ids=[f"{m} {p}" for m, p in PROTECTED])
async def test_every_protected_route_requires_token(
    api_client: AsyncClient, method: str, path: str
) -> None:
    url = _fill(path)
    no_token = await api_client.request(method, url)
    assert no_token.status_code == 401, (method, path, no_token.text)

    bad_token = await api_client.request(
        method, url, headers={"Authorization": "Bearer bozuk.token.degeri"}
    )
    assert bad_token.status_code == 401, (method, path, bad_token.text)


async def test_public_routes_stay_public(api_client: AsyncClient) -> None:
    assert (await api_client.get("/health")).status_code == 200
    assert (await api_client.get("/app")).status_code == 200


# --- 0.4: Çapraz kiracı matrisi ---------------------------------------------


async def _seed_victim(db_factory, buro_id: UUID, mukellef_id: UUID) -> dict[str, str]:
    """Büro B'de her kaynak türünden bir kayıt oluşturur ve id'lerini döner."""
    async with db_factory() as session:
        belge = Belge(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            original_filename="gizli.xml",
            content_type="application/xml",
            size_bytes=10,
            sha256="b" * 64,
            storage_key=f"victim/{uuid4()}",
            document_type="ubl_invoice",
        )
        entry = JournalEntry(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_date=date(2026, 9, 1),
            description="Gizli fiş",
            status="approved",
        )
        account = BankAccount(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            iban="TR000000000000000000000001",
            bank_name="Banka",
            account_name="Vadesiz",
            ledger_account_code="102.01",
        )
        session.add_all([belge, entry, account])
        await session.flush()
        session.add_all(
            [
                JournalLine(
                    buro_id=buro_id, mukellef_id=mukellef_id, journal_entry_id=entry.id,
                    line_order=1, account_code="100", account_name="Kasa",
                    debit=Decimal("10"), credit=Decimal("0"),
                ),
                JournalLine(
                    buro_id=buro_id, mukellef_id=mukellef_id, journal_entry_id=entry.id,
                    line_order=2, account_code="600", account_name="Satış",
                    debit=Decimal("0"), credit=Decimal("10"),
                ),
            ]
        )
        tx = BankTransaction(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            bank_account_iban=account.iban,
            external_id="victim-1",
            transaction_date=date(2026, 9, 1),
            description="Gizli hareket",
            debit=Decimal("10"),
            credit=Decimal("0"),
        )
        session.add(tx)
        await session.commit()
        return {
            "document_id": str(belge.id),
            "entry_id": str(entry.id),
            "account_id": str(account.id),
            "transaction_id": str(tx.id),
            "export_id": str(uuid4()),
            "key": "kdv_orani",
        }


@pytest.fixture
async def two_buros(db_factory):
    m_a, m_b = uuid4(), uuid4()
    buro_a = await make_buro(db_factory, mukellef_ids=[m_a])
    buro_b = await make_buro(db_factory, mukellef_ids=[m_b])
    attacker = await make_actor(db_factory, buro_a, rol="admin")
    ids = await _seed_victim(db_factory, buro_b, m_b)
    return {"buro_a": buro_a, "buro_b": buro_b, "m_a": m_a, "m_b": m_b,
            "attacker": attacker, "ids": ids}


@pytest.mark.parametrize(
    ("method", "path"), TENANT_ROUTES, ids=[f"{m} {p}" for m, p in TENANT_ROUTES]
)
async def test_foreign_mukellef_header_is_rejected(
    api_client: AsyncClient, two_buros, method: str, path: str
) -> None:
    """Büro A admini, Büro B'nin mükellefini header'la seçip X-Buro-ID'yi taklit etse de 404."""
    headers = two_buros["attacker"].headers(two_buros["m_b"])
    headers["X-Buro-ID"] = str(two_buros["buro_b"])
    res = await api_client.request(method, _fill(path, two_buros["ids"]), headers=headers)
    assert res.status_code == 404, (method, path, res.status_code, res.text)


@pytest.mark.parametrize(
    ("method", "path"), TENANT_ROUTES, ids=[f"{m} {p}" for m, p in TENANT_ROUTES]
)
async def test_foreign_resource_ids_are_not_reachable(
    api_client: AsyncClient, db_factory, two_buros, method: str, path: str
) -> None:
    """Kendi mükellefiyle gelip Büro B'nin kayıt id'lerini kullanan istek veri sızdırmaz."""
    headers = two_buros["attacker"].headers(two_buros["m_a"])
    res = await api_client.request(method, _fill(path, two_buros["ids"]), headers=headers)

    if "{" in path:
        assert not 200 <= res.status_code < 300, (method, path, res.status_code, res.text)
    assert two_buros["ids"]["document_id"] not in res.text
    assert "Gizli" not in res.text

    ids = two_buros["ids"]
    async with db_factory() as session:
        belge = await session.get(Belge, UUID(ids["document_id"]))
        entry = await session.get(JournalEntry, UUID(ids["entry_id"]))
        account = await session.get(BankAccount, UUID(ids["account_id"]))
        tx = await session.get(BankTransaction, UUID(ids["transaction_id"]))
        assert belge.review_status == "pending"
        assert entry.status == "approved"
        assert account.status == "active"
        assert tx.reconciliation_status == "unmatched"
        reversals = await session.scalars(
            select(JournalEntry).where(JournalEntry.buro_id == two_buros["buro_b"])
        )
        assert len(reversals.all()) == 1


# --- 0.7: Rol matrisi ------------------------------------------------------


@pytest.fixture
async def one_buro(db_factory):
    m1, m2 = uuid4(), uuid4()
    buro_id = await make_buro(db_factory, mukellef_ids=[m1, m2])
    actors = {
        rol: await make_actor(db_factory, buro_id, rol=rol, assigned=[m1])
        for rol in ("admin", "musavir", "personel", "mukellef")
    }
    return {"buro_id": buro_id, "m1": m1, "m2": m2, **actors}


_WRITE_ROUTES = [r for r in DATA_ROUTES if r[0] not in _SAFE]
_READ_ROUTES = [r for r in DATA_ROUTES if r[0] in _SAFE]


@pytest.mark.parametrize(
    ("method", "path"), _WRITE_ROUTES, ids=[f"{m} {p}" for m, p in _WRITE_ROUTES]
)
async def test_mukellef_role_is_read_only(
    api_client: AsyncClient, one_buro, method: str, path: str
) -> None:
    res = await api_client.request(
        method, _fill(path), headers=one_buro["mukellef"].headers(one_buro["m1"])
    )
    assert res.status_code == 403, (method, path, res.status_code, res.text)


@pytest.mark.parametrize(
    ("method", "path"), _READ_ROUTES, ids=[f"{m} {p}" for m, p in _READ_ROUTES]
)
async def test_mukellef_role_can_read_assigned(
    api_client: AsyncClient, one_buro, method: str, path: str
) -> None:
    res = await api_client.request(
        method, _fill(path), headers=one_buro["mukellef"].headers(one_buro["m1"])
    )
    assert res.status_code not in (401, 403), (method, path, res.status_code, res.text)


@pytest.mark.parametrize(
    ("method", "path"), sorted(APPROVER_ONLY), ids=[f"{m} {p}" for m, p in sorted(APPROVER_ONLY)]
)
async def test_personel_cannot_run_approver_actions(
    api_client: AsyncClient, one_buro, method: str, path: str
) -> None:
    res = await api_client.request(
        method, _fill(path), headers=one_buro["personel"].headers(one_buro["m1"])
    )
    assert res.status_code == 403
    allowed = await api_client.request(
        method, _fill(path), headers=one_buro["musavir"].headers(one_buro["m1"])
    )
    assert allowed.status_code != 403


async def test_assignment_controls_mukellef_access(api_client: AsyncClient, one_buro) -> None:
    for rol in ("personel", "mukellef"):
        actor = one_buro[rol]
        assigned = await api_client.get("/ledger/entries", headers=actor.headers(one_buro["m1"]))
        unassigned = await api_client.get(
            "/ledger/entries", headers=actor.headers(one_buro["m2"])
        )
        assert assigned.status_code == 200, rol
        assert unassigned.status_code == 404, rol
    for rol in ("admin", "musavir"):
        res = await api_client.get(
            "/ledger/entries", headers=one_buro[rol].headers(one_buro["m2"])
        )
        assert res.status_code == 200, rol


async def test_only_admin_manages_users(api_client: AsyncClient, one_buro) -> None:
    for rol in ("musavir", "personel", "mukellef"):
        res = await api_client.get("/auth/users", headers=one_buro[rol].headers())
        assert res.status_code == 403, rol
    assert (await api_client.get("/auth/users", headers=one_buro["admin"].headers())).status_code == 200
