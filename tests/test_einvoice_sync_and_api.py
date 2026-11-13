from datetime import date
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base
from app.einvoice import (
    EInvoiceQuery,
    FakeEInvoiceAdapter,
    sync_incoming_invoices,
)


class InMemoryStorage:
    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self._files[key] = data

    async def get(self, key: str) -> bytes:
        return self._files[key]

    async def delete(self, key: str) -> None:
        self._files.pop(key, None)


@pytest.mark.asyncio
async def test_sync_incoming_invoices_imports_and_extracts() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    adapter = FakeEInvoiceAdapter()
    ubl_content = b"""<Invoice>
      <ID>GIB2026000000001</ID>
      <IssueDate>2026-11-04</IssueDate>
      <DocumentCurrencyCode>TRY</DocumentCurrencyCode>
      <LegalMonetaryTotal>
        <PayableAmount>1000.00</PayableAmount>
      </LegalMonetaryTotal>
    </Invoice>"""

    doc = await adapter.issue_invoice(ubl_content)
    storage = InMemoryStorage()

    async with factory() as session:
        query = EInvoiceQuery(date(2026, 11, 1), date(2026, 11, 4))
        result = await sync_incoming_invoices(
            session=session,
            adapter=adapter,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            query=query,
            storage=storage,
        )

        assert result.total_fetched == 1
        assert result.imported_count == 1
        assert result.skipped_count == 0

        # Duplicate sync test
        second_result = await sync_incoming_invoices(
            session=session,
            adapter=adapter,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            query=query,
            storage=storage,
        )
        assert second_result.total_fetched == 1
        assert second_result.imported_count == 0
        assert second_result.skipped_count == 1

    await engine.dispose()


async def test_einvoice_api_endpoints(api_client: AsyncClient, db_factory) -> None:
    from tests.auth_helpers import make_actor, make_buro

    assert (await api_client.get("/einvoice/providers")).status_code == 401
    buro_id = await make_buro(db_factory)
    actor = await make_actor(db_factory, buro_id, rol="personel")
    res = await api_client.get("/einvoice/providers", headers=actor.headers())
    assert res.status_code == 200
    assert "fake" in res.json()["providers"]
