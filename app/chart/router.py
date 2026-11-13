"""Hesap planı / mizan uçları."""

import re
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.auth.deps import get_current_user
from app.auth.deps import tenant_context as tenant_headers
from app.chart.import_service import import_mizan_rows
from app.chart.mizan_upload import MizanParseError, parse_mizan_file
from app.core.db import get_session
from app.core.models import Hesap, User
from app.core.safety import UnsafeArchiveError, check_zip_archive

router = APIRouter(prefix="/chart", tags=["chart"])
MAX_MIZAN_SIZE = 10 * 1024 * 1024


@router.post("/mizan")
async def upload_mizan(
    request: Request,
    file: UploadFile,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    """Mizanı (Excel/CSV) okuyup mükellefin hesap planına aktarır (tekrar yükleme günceller)."""
    buro_id, mukellef_id = tenant
    content = await file.read(MAX_MIZAN_SIZE + 1)
    if len(content) > MAX_MIZAN_SIZE:
        raise HTTPException(status_code=413, detail="Mizan dosyası 10 MB sınırını aşamaz.")
    if content[:2] == b"PK":
        try:
            check_zip_archive(content)
        except UnsafeArchiveError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        parsed = parse_mizan_file(content, file.filename or "")
    except MizanParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Aynı kod birden fazla kez geçerse son satır geçerli olur.
    unique = list({row.hesap_kodu: row for row in parsed.rows}.values())
    summary = await import_mizan_rows(session, buro_id, mukellef_id, unique)
    await audit.record(
        session, "chart.mizan_upload", request=request, user=user, mukellef_id=mukellef_id,
        target_type="mizan", details={
            "filename": file.filename, "accounts": len(unique), "balanced": parsed.balanced,
        },
    )
    return {
        "accounts": len(unique),
        "inserted": summary.inserted,
        "updated": summary.updated,
        "unchanged": summary.unchanged,
        "total_debit": str(parsed.total_debit),
        "total_credit": str(parsed.total_credit),
        "difference": str(parsed.difference),
        "balanced": parsed.balanced,
        "method": parsed.method,
        "warnings": parsed.warnings,
        "message": (
            f"{len(unique)} hesap aktarıldı ({summary.inserted} yeni, {summary.updated} güncellendi). "
            + ("Mizan tutuyor." if parsed.balanced else f"Mizan tutmuyor: fark {parsed.difference}.")
        ),
    }


@router.get("/summary")
async def chart_summary(
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Yüklü mizanın özeti: hesap sayısı ve ana hesaplara göre denge."""
    buro_id, mukellef_id = tenant
    base = select(Hesap).where(Hesap.buro_id == buro_id, Hesap.mukellef_id == mukellef_id)
    count = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (await session.scalars(base)).all()
    main = [r for r in rows if re.fullmatch(r"\d{3}", r.kod)] or rows
    debit = sum((r.acilis_borcu or Decimal("0") for r in main), Decimal("0"))
    credit = sum((r.acilis_alacagi or Decimal("0") for r in main), Decimal("0"))
    return {
        "accounts": count or 0,
        "total_debit": str(debit),
        "total_credit": str(credit),
        "balanced": bool(count) and debit == credit,
    }
