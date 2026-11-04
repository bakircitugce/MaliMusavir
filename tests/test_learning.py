from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import Base, Mukellef
from app.learning import record_feedback, suggest_associations


@pytest.mark.asyncio
async def test_learning_requires_repeated_accepted_feedback_before_suggestion() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with factory() as session:
        session.add(
            Mukellef(
                buro_id=buro_id,
                vkn_tckn="12345678901",
                unvan="Örnek Mükellef",
                mukellef_turu="sahis",
            )
        )
        await session.flush()
        for accepted in (True, True, True):
            await record_feedback(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                feature_type="description",
                feature_value="Kira ödemesi",
                label="770",
                accepted=accepted,
            )

        suggestions = await suggest_associations(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            feature_type="description",
            feature_value="  KİRA   ÖDEMESİ ",
        )
        assert len(suggestions) == 1
        assert suggestions[0].label == "770"
        assert suggestions[0].observations == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_learning_does_not_suggest_low_confidence_association() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        for accepted in (True, False, False):
            await record_feedback(
                session,
                buro_id=uuid4(),
                mukellef_id=uuid4(),
                feature_type="description",
                feature_value="Farklı tenant",
                label="600",
                accepted=accepted,
            )
        suggestions = await suggest_associations(
            session,
            buro_id=uuid4(),
            mukellef_id=uuid4(),
            feature_type="description",
            feature_value="Farklı tenant",
        )
        assert suggestions == ()
    await engine.dispose()
