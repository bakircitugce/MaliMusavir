from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.models import Mukellef
from app.einvoice.adapter import EInvoiceQuery
from app.einvoice.registry import einvoice_registry
from app.einvoice.fake import FakeEInvoiceAdapter
from app.einvoice.submission import submit_ubl_invoice
from app.einvoice.sync_service import sync_incoming_invoices

# Fake adapter'ı kaydet
if "fake" not in einvoice_registry.providers():
    einvoice_registry.register("fake", FakeEInvoiceAdapter)

router = APIRouter(prefix="/einvoice", tags=["einvoice"])


async def tenant_headers(
    x_buro_id: UUID | None = Header(default=None),
    x_mukellef_id: UUID | None = Header(default=None),
) -> tuple[UUID, UUID]:
    if x_buro_id is None or x_mukellef_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Buro-ID ve X-Mukellef-ID header'ları zorunludur.",
        )
    return x_buro_id, x_mukellef_id


async def validate_tenant(
    session: AsyncSession,
    buro_id: UUID,
    mukellef_id: UUID,
) -> None:
    tenant = await session.scalar(
        select(Mukellef).where(Mukellef.id == mukellef_id, Mukellef.buro_id == buro_id)
    )
    if tenant is None:
        raise HTTPException(status_code=404, detail="Büro veya mükellef bulunamadı.")


class SyncRequest(BaseModel):
    provider: str = "fake"
    start_date: date
    end_date: date
    page_size: int = 100


class SubmitRequest(BaseModel):
    provider: str = "fake"
    ubl_content: str
    approved: bool = False


@router.get("/providers")
async def get_providers() -> dict[str, object]:
    return {"providers": list(einvoice_registry.providers())}


@router.post("/sync")
async def sync_invoices(
    req: SyncRequest,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)

    try:
        adapter = einvoice_registry.create(req.provider)
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        query = EInvoiceQuery(
            start_date=req.start_date,
            end_date=req.end_date,
            page=1,
            page_size=req.page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await sync_incoming_invoices(
        session=session,
        adapter=adapter,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        query=query,
    )

    return {
        "ok": True,
        "provider": req.provider,
        "total_fetched": result.total_fetched,
        "imported_count": result.imported_count,
        "skipped_count": result.skipped_count,
        "extracted_count": result.extracted_count,
    }


@router.post("/submit")
async def submit_invoice(
    req: SubmitRequest,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)

    try:
        adapter = einvoice_registry.create(req.provider)
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        doc = await submit_ubl_invoice(
            adapter=adapter,
            ubl_document=req.ubl_content.encode("utf-8"),
            approved=req.approved,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "ettn": doc.ettn,
        "invoice_number": doc.invoice_number,
        "provider_id": doc.provider_id,
    }
