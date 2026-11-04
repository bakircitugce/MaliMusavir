import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import LearnedAssociation


MIN_OBSERVATIONS = 3
SUGGESTION_THRESHOLD = Decimal("0.80")


def feature_hash(feature_value: str) -> str:
    folded = unicodedata.normalize("NFKD", feature_value.strip().casefold())
    without_marks = "".join(
        character for character in folded if not unicodedata.combining(character)
    )
    normalized = re.sub(r"\s+", " ", without_marks)
    if not normalized:
        raise ValueError("Öğrenme özelliği boş olamaz.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LearningSuggestion:
    feature_type: str
    label: str
    confidence: Decimal
    observations: int


async def record_feedback(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    feature_type: str,
    feature_value: str,
    label: str,
    accepted: bool,
) -> LearnedAssociation:
    normalized_type = feature_type.strip()
    normalized_label = label.strip()
    if not normalized_type or not normalized_label:
        raise ValueError("Öğrenme özellik türü ve etiketi boş olamaz.")
    hashed = feature_hash(feature_value)
    association = await session.scalar(
        select(LearnedAssociation).where(
            LearnedAssociation.buro_id == buro_id,
            LearnedAssociation.mukellef_id == mukellef_id,
            LearnedAssociation.feature_type == normalized_type,
            LearnedAssociation.feature_hash == hashed,
            LearnedAssociation.label == normalized_label,
        )
    )
    if association is None:
        association = LearnedAssociation(
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            feature_type=normalized_type,
            feature_hash=hashed,
            label=normalized_label,
        )
        session.add(association)
        await session.flush()
    if accepted:
        association.positive_count += 1
    else:
        association.negative_count += 1
    total = association.positive_count + association.negative_count
    association.confidence = Decimal(association.positive_count) / Decimal(total)
    await session.flush()
    return association


async def suggest_associations(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    feature_type: str,
    feature_value: str,
) -> tuple[LearningSuggestion, ...]:
    hashed = feature_hash(feature_value)
    associations = list(
        (
            await session.scalars(
                select(LearnedAssociation)
                .where(
                    LearnedAssociation.buro_id == buro_id,
                    LearnedAssociation.mukellef_id == mukellef_id,
                    LearnedAssociation.feature_type == feature_type.strip(),
                    LearnedAssociation.feature_hash == hashed,
                    LearnedAssociation.confidence >= SUGGESTION_THRESHOLD,
                )
                .order_by(LearnedAssociation.confidence.desc())
            )
        ).all()
    )
    return tuple(
        LearningSuggestion(
            feature_type=association.feature_type,
            label=association.label,
            confidence=association.confidence,
            observations=association.positive_count + association.negative_count,
        )
        for association in associations
        if association.positive_count + association.negative_count >= MIN_OBSERVATIONS
    )
