import asyncio

from sqlalchemy import select

from app.core.db import session_factory
from app.core.models import Buro, Mukellef


async def bootstrap_demo_tenant() -> None:
    async with session_factory() as session:
        buro = await session.scalar(select(Buro).where(Buro.unvan == "Demo SMMM Burosu"))
        if buro is None:
            buro = Buro(unvan="Demo SMMM Burosu")
            session.add(buro)
            await session.flush()

        mukellef = await session.scalar(
            select(Mukellef).where(
                Mukellef.buro_id == buro.id,
                Mukellef.unvan == "Demo Mukellef",
            )
        )
        if mukellef is None:
            mukellef = Mukellef(
                buro_id=buro.id,
                vkn_tckn="0000000000",
                unvan="Demo Mukellef",
                mukellef_turu="kurumlar",
            )
            session.add(mukellef)

        await session.commit()
        print(f"BURO_ID={buro.id}")
        print(f"MUKELLEF_ID={mukellef.id}")


if __name__ == "__main__":
    asyncio.run(bootstrap_demo_tenant())
