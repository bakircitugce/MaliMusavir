from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_approver
from app.auth.deps import tenant_context as tenant_headers
from app.core.db import get_session
from app.core.models import Mukellef
from app.erp import (
    erp_registry,
    export_approved_entry,
    get_export_record,
    list_export_records,
    retry_export,
)

router = APIRouter(prefix="/erp", tags=["erp"])




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


@router.get("/exports")
async def get_exports(
    status: str | None = Query(default=None),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        records = await list_export_records(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "exports": [
            {
                "id": str(record.id),
                "journal_entry_id": str(record.journal_entry_id),
                "idempotency_key": record.idempotency_key,
                "status": record.status,
                "external_reference": record.external_reference,
                "error_message": record.error_message,
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
            }
            for record in records
        ]
    }


@router.post("/exports/{export_id}/retry", dependencies=[Depends(require_approver)])
async def retry_failed_export(
    export_id: UUID,
    provider: str = Query(min_length=1),
    max_attempts: int = Query(default=3, ge=1, le=10),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    record = await get_export_record(
        session,
        export_id=export_id,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="ERP aktarım kaydı bulunamadı.")
    if record.status != "failed":
        raise HTTPException(
            status_code=409,
            detail="Yalnızca başarısız ERP aktarımları yeniden denenebilir.",
        )
    try:
        adapter = erp_registry.create(provider)
    except LookupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        result = await retry_export(
            lambda: export_approved_entry(
                session,
                adapter,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                journal_entry_id=record.journal_entry_id,
            ),
            max_attempts=max_attempts,
        )
    except Exception as exc:
        await session.commit()
        raise HTTPException(
            status_code=502,
            detail=f"ERP aktarımı yeniden denenemedi: {exc}",
        ) from exc
    await session.commit()
    return {
        "ok": True,
        "export_id": str(record.id),
        "status": "exported",
        "external_reference": result.external_reference,
        "was_duplicate": result.was_duplicate,
    }
