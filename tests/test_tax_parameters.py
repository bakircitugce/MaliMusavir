from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base, TaxParameter
from app.tax import list_tax_parameters, resolve_tax_parameter


@pytest.mark.asyncio
async def test_resolve_tax_parameter_is_tenant_and_date_scoped() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with factory() as session:
        session.add_all(
            [
                TaxParameter(
                    buro_id=buro_id,
                    anahtar="kdv_orani",
                    deger=Decimal("20"),
                    baslangic_tarihi=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    bitis_tarihi=datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc),
                    metadata_json={"source": "official"},
                ),
                TaxParameter(
                    buro_id=uuid4(),
                    anahtar="kdv_orani",
                    deger=Decimal("99"),
                    baslangic_tarihi=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    bitis_tarihi=None,
                ),
            ]
        )
        await session.flush()

        parameter = await resolve_tax_parameter(
            session,
            buro_id=buro_id,
            key=" kdv_orani ",
            at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )

        assert parameter is not None
        assert parameter.deger == Decimal("20.000000")
        assert parameter.metadata_json == {"source": "official"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_tax_parameter_rejects_overlapping_periods() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with factory() as session:
        for start in (datetime(2026, 1, 1), datetime(2026, 6, 1)):
            session.add(
                TaxParameter(
                    buro_id=buro_id,
                    anahtar="oran",
                    deger=Decimal("1"),
                    baslangic_tarihi=start,
                    bitis_tarihi=None,
                )
            )
        await session.flush()

        with pytest.raises(ValueError, match="çakışan"):
            await resolve_tax_parameter(
                session,
                buro_id=buro_id,
                key="oran",
                at=datetime(2026, 7, 1),
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_tax_parameters_isolated_by_buro_and_filters_key() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    buro_id = uuid4()
    async with factory() as session:
        session.add_all(
            [
                TaxParameter(
                    buro_id=buro_id,
                    anahtar="kdv_orani",
                    deger=Decimal("20"),
                    baslangic_tarihi=datetime(2026, 1, 1),
                    bitis_tarihi=None,
                ),
                TaxParameter(
                    buro_id=buro_id,
                    anahtar="stopaj_orani",
                    deger=Decimal("15"),
                    baslangic_tarihi=datetime(2026, 1, 1),
                    bitis_tarihi=None,
                ),
                TaxParameter(
                    buro_id=uuid4(),
                    anahtar="kdv_orani",
                    deger=Decimal("99"),
                    baslangic_tarihi=datetime(2026, 1, 1),
                    bitis_tarihi=None,
                ),
            ]
        )
        await session.flush()

        parameters = await list_tax_parameters(
            session,
            buro_id=buro_id,
            key=" kdv_orani ",
        )

        assert len(parameters) == 1
        assert parameters[0].deger == Decimal("20.000000")
    await engine.dispose()
