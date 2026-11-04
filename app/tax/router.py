from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.models import Mukellef
from app.tax import list_tax_parameters, resolve_tax_parameter
from app.tax.summary import summarize_approved_entries

router = APIRouter(prefix="/tax", tags=["tax"])


class TaxSummaryRequest(BaseModel):
    start_date: date
    end_date: date
    account_prefixes: dict[str, list[str]] = Field(min_length=1)


async def tenant_headers(
    x_buro_id: UUID | None = Header(default=None),
    x_mukellef_id: UUID | None = Header(default=None),
) -> tuple[UUID, UUID]:
    if x_buro_id is None or x_mukellef_id is None:
        raise HTTPException(
            status_code=400,
            detail="X-Buro-ID ve X-Mukellef-ID header'ları zorunludur.",
        )
    return x_buro_id, x_mukellef_id


@router.post("/summaries")
async def tax_summary(
    request: TaxSummaryRequest,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    exists = await session.scalar(
        select(Mukellef.id).where(
            Mukellef.id == mukellef_id,
            Mukellef.buro_id == buro_id,
        )
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="Büro veya mükellef bulunamadı.")
    try:
        lines = await summarize_approved_entries(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            start_date=request.start_date,
            end_date=request.end_date,
            account_prefixes={
                category: tuple(prefixes)
                for category, prefixes in request.account_prefixes.items()
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "lines": [
            {
                "category": line.category,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "net": str(line.net),
                "journal_entry_ids": [str(entry_id) for entry_id in line.journal_entry_ids],
            }
            for line in lines
        ],
    }


@router.get("/parameters/{key}")
async def get_tax_parameter(
    key: str,
    at: datetime | None = None,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    exists = await session.scalar(
        select(Mukellef.id).where(
            Mukellef.id == mukellef_id,
            Mukellef.buro_id == buro_id,
        )
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="Büro veya mükellef bulunamadı.")
    lookup_time = at or datetime.now(timezone.utc)
    try:
        parameter = await resolve_tax_parameter(
            session,
            buro_id=buro_id,
            key=key,
            at=lookup_time,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if parameter is None:
        raise HTTPException(status_code=404, detail="Vergi parametresi bulunamadı.")
    return {
        "key": parameter.anahtar,
        "value": str(parameter.deger),
        "start": parameter.baslangic_tarihi.isoformat(),
        "end": (
            parameter.bitis_tarihi.isoformat()
            if parameter.bitis_tarihi is not None
            else None
        ),
        "metadata": parameter.metadata_json,
    }


@router.get("/parameters")
async def list_parameters(
    key: str | None = None,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    exists = await session.scalar(
        select(Mukellef.id).where(
            Mukellef.id == mukellef_id,
            Mukellef.buro_id == buro_id,
        )
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="Büro veya mükellef bulunamadı.")
    try:
        parameters = await list_tax_parameters(
            session,
            buro_id=buro_id,
            key=key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "parameters": [
            {
                "key": parameter.anahtar,
                "value": str(parameter.deger),
                "start": parameter.baslangic_tarihi.isoformat(),
                "end": (
                    parameter.bitis_tarihi.isoformat()
                    if parameter.bitis_tarihi is not None
                    else None
                ),
                "metadata": parameter.metadata_json,
            }
            for parameter in parameters
        ]
    }
