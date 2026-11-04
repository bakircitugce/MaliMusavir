import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import JournalEntry, JournalLine
from app.erp.adapter import ERPExportAdapter, ERPExportDocument, ERPExportResult
from app.erp.tracking import (
    get_or_create_export,
    record_export_failure,
    record_export_result,
)
from app.ledger.draft import LedgerValidationError


async def export_approved_entry(
    session: AsyncSession,
    adapter: ERPExportAdapter,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    journal_entry_id: UUID,
) -> ERPExportResult:
    entry = await session.scalar(
        select(JournalEntry).where(
            JournalEntry.id == journal_entry_id,
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
        )
    )
    if entry is None:
        raise LedgerValidationError("ERP aktarımı için yevmiye fişi bulunamadı.")
    if entry.status != "approved":
        raise LedgerValidationError("Yalnızca onaylı yevmiye fişleri ERP'ye aktarılabilir.")
    lines = list(
        (
            await session.scalars(
                select(JournalLine)
                .where(
                    JournalLine.journal_entry_id == entry.id,
                    JournalLine.buro_id == buro_id,
                    JournalLine.mukellef_id == mukellef_id,
                )
                .order_by(JournalLine.line_order)
            )
        ).all()
    )
    if not lines:
        raise LedgerValidationError("ERP aktarımı için yevmiye fişinde satır bulunamadı.")
    payload = json.dumps(
        {
            "entry_date": entry.entry_date.isoformat(),
            "description": entry.description,
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
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    document = ERPExportDocument(
        idempotency_key=f"{buro_id}:{mukellef_id}:{entry.id}",
        journal_entry_id=entry.id,
        payload=payload,
    )
    await get_or_create_export(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        journal_entry_id=entry.id,
        idempotency_key=document.idempotency_key,
    )
    try:
        result = await adapter.export_entry(document)
    except Exception as exc:
        await record_export_failure(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            journal_entry_id=entry.id,
            idempotency_key=document.idempotency_key,
            error_message=str(exc),
        )
        raise
    await record_export_result(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        journal_entry_id=entry.id,
        idempotency_key=document.idempotency_key,
        result=result,
    )
    return result
