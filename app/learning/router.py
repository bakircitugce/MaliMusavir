from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import tenant_context as tenant_headers
from app.core.db import get_session
from app.core.models import Mukellef
from app.learning import record_feedback, suggest_associations

router = APIRouter(prefix="/learning", tags=["learning"])


class FeedbackRequest(BaseModel):
    feature_type: str = Field(min_length=1, max_length=50)
    feature_value: str = Field(min_length=1, max_length=1000)
    label: str = Field(min_length=1, max_length=100)
    accepted: bool


class SuggestionRequest(BaseModel):
    feature_type: str = Field(min_length=1, max_length=50)
    feature_value: str = Field(min_length=1, max_length=1000)




async def validate_tenant(
    session: AsyncSession,
    buro_id: UUID,
    mukellef_id: UUID,
) -> None:
    exists = await session.scalar(
        select(Mukellef.id).where(
            Mukellef.id == mukellef_id,
            Mukellef.buro_id == buro_id,
        )
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="Büro veya mükellef bulunamadı.")


@router.post("/feedback")
async def learning_feedback(
    request: FeedbackRequest,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        association = await record_feedback(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            feature_type=request.feature_type,
            feature_value=request.feature_value,
            label=request.label,
            accepted=request.accepted,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {
        "ok": True,
        "label": association.label,
        "positive_count": association.positive_count,
        "negative_count": association.negative_count,
        "confidence": str(association.confidence),
        "ready_for_suggestion": (
            association.positive_count + association.negative_count >= 3
            and association.confidence >= Decimal("0.80")
        ),
    }


@router.post("/suggestions")
async def learning_suggestions(
    request: SuggestionRequest,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        suggestions = await suggest_associations(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            feature_type=request.feature_type,
            feature_value=request.feature_value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "suggestions": [
            {
                "feature_type": item.feature_type,
                "label": item.label,
                "confidence": str(item.confidence),
                "observations": item.observations,
            }
            for item in suggestions
        ]
    }
