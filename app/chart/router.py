"""Hesap planı / mizan uçları."""

import re
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.auth.deps import get_current_user
from app.auth.deps import tenant_context as tenant_headers
from app.chart.cari import get_or_create_cari
from app.chart.import_service import import_mizan_rows
from app.chart.mizan_upload import MizanParseError, parse_mizan_file
from app.core.db import get_session
from app.core.models import Cari, Hesap, JournalEntry, JournalLine, User
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


class CariInput(BaseModel):
    ana_hesap: str = Field(pattern=r"^(120|320|136|336|159|340)$")
    unvan: str = Field(min_length=2, max_length=250)
    vkn_tckn: str | None = Field(default=None, pattern=r"^\d{10,11}$")
    hesap_kodu: str | None = Field(default=None, pattern=r"^\d{3}(\.[0-9A-Za-z]{1,20}){1,4}$")


def _serialize_cari(card: Cari, balance: tuple[Decimal, Decimal] | None = None) -> dict[str, object]:
    debit, credit = balance or (Decimal("0"), Decimal("0"))
    return {
        "id": str(card.id),
        "ana_hesap": card.ana_hesap,
        "hesap_kodu": card.hesap_kodu,
        "unvan": card.unvan,
        "vkn_tckn": card.vkn_tckn,
        "otomatik": card.otomatik,
        "borc": str(debit),
        "alacak": str(credit),
        "bakiye": str(debit - credit),
    }


@router.get("/cari")
async def list_cari(
    ana_hesap: str | None = Query(default=None, pattern=r"^\d{3}$"),
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Mükellefin cari kartları ve onaylı fişlerden hesaplanan bakiyeleri."""
    buro_id, mukellef_id = tenant
    stmt = select(Cari).where(Cari.buro_id == buro_id, Cari.mukellef_id == mukellef_id)
    if ana_hesap:
        stmt = stmt.where(Cari.ana_hesap == ana_hesap)
    cards = (await session.scalars(stmt.order_by(Cari.hesap_kodu))).all()
    totals = {
        code: (debit or Decimal("0"), credit or Decimal("0"))
        for code, debit, credit in (
            await session.execute(
                select(JournalLine.account_code, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
                .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
                .where(
                    JournalLine.buro_id == buro_id,
                    JournalLine.mukellef_id == mukellef_id,
                    JournalEntry.status == "approved",
                )
                .group_by(JournalLine.account_code)
            )
        ).all()
    }
    return {"cari": [_serialize_cari(c, totals.get(c.hesap_kodu)) for c in cards]}


@router.post("/cari", status_code=201)
async def create_cari(
    body: CariInput,
    request: Request,
    tenant: tuple[UUID, UUID] = Depends(tenant_headers),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, object]:
    """Elle cari kartı açar. Kod verilmezse mükellefin numaralandırmasından ardışık atanır."""
    buro_id, mukellef_id = tenant
    if body.hesap_kodu and not body.hesap_kodu.startswith(body.ana_hesap + "."):
        raise HTTPException(status_code=422, detail="Hesap kodu seçilen ana hesap altında olmalıdır.")
    if body.vkn_tckn and await session.scalar(
        select(Cari.id).where(
            Cari.buro_id == buro_id, Cari.mukellef_id == mukellef_id,
            Cari.ana_hesap == body.ana_hesap, Cari.vkn_tckn == body.vkn_tckn,
        )
    ):
        raise HTTPException(status_code=409, detail="Bu VKN/TCKN için cari zaten var.")
    if body.hesap_kodu:
        if await session.scalar(
            select(Cari.id).where(Cari.mukellef_id == mukellef_id, Cari.hesap_kodu == body.hesap_kodu)
        ):
            raise HTTPException(status_code=409, detail="Bu hesap kodu başka bir cariye bağlı.")
        if await session.scalar(
            select(Hesap.id).where(Hesap.mukellef_id == mukellef_id, Hesap.kod == body.hesap_kodu)
        ) is None:
            session.add(Hesap(
                buro_id=buro_id, mukellef_id=mukellef_id, kod=body.hesap_kodu, ad=body.unvan,
                seviye=len(body.hesap_kodu.split(".")), ust_kod=body.hesap_kodu.rsplit(".", 1)[0],
            ))
        card = Cari(
            buro_id=buro_id, mukellef_id=mukellef_id, ana_hesap=body.ana_hesap,
            hesap_kodu=body.hesap_kodu, unvan=body.unvan, vkn_tckn=body.vkn_tckn, otomatik=False,
        )
        session.add(card)
        await session.flush()
    else:
        result = await get_or_create_cari(
            session, buro_id=buro_id, mukellef_id=mukellef_id,
            main_account=body.ana_hesap, unvan=body.unvan, vkn_tckn=body.vkn_tckn,
        )
        card = result.cari
        card.otomatik = False
    await audit.record(
        session, "chart.cari_create", request=request, user=user, mukellef_id=mukellef_id,
        target_type="cari", target_id=card.hesap_kodu, details={"unvan": card.unvan}, commit=False,
    )
    await session.commit()
    return _serialize_cari(card)
