from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.models import Mukellef
from app.ledger.draft import LedgerValidationError
from app.ledger.service import (
    get_entry_with_lines,
    list_entries,
    persist_reversal,
)
from app.ledger.trial_balance import trial_balance

router = APIRouter(prefix="/ledger", tags=["ledger"])


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


@router.get("/entries")
async def get_entries(
    status: str | None = Query(default=None),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        entries = await list_entries(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            status=status,
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "entries": [
            {
                "id": str(e.id),
                "entry_date": e.entry_date.isoformat(),
                "description": e.description,
                "status": e.status,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ]
    }


@router.get("/entries/{entry_id}")
async def get_entry_detail(
    entry_id: UUID,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        entry, lines = await get_entry_with_lines(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_id=entry_id,
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    total_debit = sum(line.debit for line in lines)
    total_credit = sum(line.credit for line in lines)

    return {
        "id": str(entry.id),
        "entry_date": entry.entry_date.isoformat(),
        "description": entry.description,
        "status": entry.status,
        "total_debit": str(total_debit),
        "total_credit": str(total_credit),
        "lines": [
            {
                "order": line.line_order,
                "account_code": line.account_code,
                "account_name": line.account_name,
                "debit": str(line.debit),
                "credit": str(line.credit),
            }
            for line in lines
        ],
    }


@router.get("/trial-balance")
async def get_trial_balance(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    status: str | None = Query(default="approved"),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    balances = await trial_balance(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        start_date=start_date,
        end_date=end_date,
        status=status,
    )

    grand_total_debit = Decimal("0.00")
    grand_total_credit = Decimal("0.00")
    rows = []
    for b in balances:
        grand_total_debit += b.total_debit
        grand_total_credit += b.total_credit
        rows.append(
            {
                "account_code": b.account_code,
                "account_name": b.account_name,
                "total_debit": str(b.total_debit),
                "total_credit": str(b.total_credit),
                "balance": str(b.balance),
            }
        )

    return {
        "trial_balance": rows,
        "grand_total_debit": str(grand_total_debit),
        "grand_total_credit": str(grand_total_credit),
        "is_balanced": grand_total_debit == grand_total_credit,
    }


@router.post("/entries/{entry_id}/reverse")
async def reverse_entry(
    entry_id: UUID,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    buro_id, mukellef_id = tenant
    await validate_tenant(session, buro_id, mukellef_id)
    try:
        reversal = await persist_reversal(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_id=entry_id,
        )
    except LedgerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    return {
        "ok": True,
        "reversed_entry_id": str(entry_id),
        "reversal_entry_id": str(reversal.id),
        "status": reversal.status,
    }
