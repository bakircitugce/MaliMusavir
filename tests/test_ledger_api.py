from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import get_session
from app.core.models import Base, JournalEntry, JournalLine, Mukellef
from app.main import app


@pytest.mark.asyncio
async def test_ledger_api_endpoints() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    headers = {
        "X-Buro-ID": str(buro_id),
        "X-Mukellef-ID": str(mukellef_id),
    }

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Requires tenant headers
            no_auth = await client.get("/ledger/entries")
            assert no_auth.status_code == 400

            # Tenant not found (404)
            not_found = await client.get("/ledger/entries", headers=headers)
            assert not_found.status_code == 404

            # Create tenant
            async with factory() as session:
                session.add(
                    Mukellef(
                        id=mukellef_id,
                        buro_id=buro_id,
                        vkn_tckn="1234567890",
                        unvan="Test Ltd.",
                        vergi_dairesi="Kadıköy",
                        mukellef_turu="tuzel",
                        muhasebe_sistemi="bilanco",
                        defter_turu="e_defter",
                    )
                )
                await session.commit()

            # Now tenant exists, returns empty list
            res = await client.get("/ledger/entries", headers=headers)
            assert res.status_code == 200
            assert res.json() == {"entries": []}

            # Trial balance
            tb = await client.get("/ledger/trial-balance", headers=headers)
            assert tb.status_code == 200
            assert tb.json()["is_balanced"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()
