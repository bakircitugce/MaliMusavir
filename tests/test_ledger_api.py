from uuid import uuid4

from httpx import AsyncClient

from app.core.models import Mukellef
from tests.auth_helpers import make_actor, make_buro


async def test_ledger_api_endpoints(api_client: AsyncClient, db_factory) -> None:
    buro_id = await make_buro(db_factory)
    admin = await make_actor(db_factory, buro_id, rol="admin")
    mukellef_id = uuid4()

    # Token yok -> 401
    assert (await api_client.get("/ledger/entries")).status_code == 401

    # Mükellef header'ı yok -> 400
    assert (await api_client.get("/ledger/entries", headers=admin.headers())).status_code == 400

    # Mükellef yok -> 404
    not_found = await api_client.get("/ledger/entries", headers=admin.headers(mukellef_id))
    assert not_found.status_code == 404

    async with db_factory() as session:
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

    res = await api_client.get("/ledger/entries", headers=admin.headers(mukellef_id))
    assert res.status_code == 200
    assert res.json() == {"entries": []}

    tb = await api_client.get("/ledger/trial-balance", headers=admin.headers(mukellef_id))
    assert tb.status_code == 200
    assert tb.json()["is_balanced"] is True
