from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import TaxParameter


async def resolve_tax_parameter(
    session: AsyncSession,
    *,
    buro_id: UUID,
    key: str,
    at: datetime,
) -> TaxParameter | None:
    normalized_key = key.strip()
    if not normalized_key:
        raise ValueError("Vergi parametresi anahtarı boş olamaz.")
    parameters = list(
        (
            await session.scalars(
                select(TaxParameter)
                .where(
                    TaxParameter.buro_id == buro_id,
                    TaxParameter.anahtar == normalized_key,
                    TaxParameter.baslangic_tarihi <= at,
                    or_(
                        TaxParameter.bitis_tarihi.is_(None),
                        TaxParameter.bitis_tarihi >= at,
                    ),
                )
                .order_by(TaxParameter.baslangic_tarihi.desc())
            )
        ).all()
    )
    if len(parameters) > 1:
        raise ValueError(
            f"Vergi parametresi için çakışan dönemler bulundu: {normalized_key}"
        )
    return parameters[0] if parameters else None


async def list_tax_parameters(
    session: AsyncSession,
    *,
    buro_id: UUID,
    key: str | None = None,
) -> list[TaxParameter]:
    statement = select(TaxParameter).where(TaxParameter.buro_id == buro_id)
    if key is not None:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("Vergi parametresi anahtarı boş olamaz.")
        statement = statement.where(TaxParameter.anahtar == normalized_key)
    statement = statement.order_by(
        TaxParameter.anahtar,
        TaxParameter.baslangic_tarihi.desc(),
    )
    return list((await session.scalars(statement)).all())
