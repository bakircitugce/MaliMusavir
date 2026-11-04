from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import ERPExport
from app.erp.adapter import ERPExportResult


async def get_or_create_export(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    journal_entry_id: UUID,
    idempotency_key: str,
) -> ERPExport:
    record = await session.scalar(
        select(ERPExport).where(
            ERPExport.buro_id == buro_id,
            ERPExport.mukellef_id == mukellef_id,
            ERPExport.journal_entry_id == journal_entry_id,
            ERPExport.idempotency_key == idempotency_key,
        )
    )
    if record is None:
        record = ERPExport(
            id=uuid4(),
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            journal_entry_id=journal_entry_id,
            idempotency_key=idempotency_key,
            status="pending",
        )
        session.add(record)
        await session.flush()
    return record


async def record_export_result(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    journal_entry_id: UUID,
    idempotency_key: str,
    result: ERPExportResult,
) -> ERPExport:
    record = await get_or_create_export(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        journal_entry_id=journal_entry_id,
        idempotency_key=idempotency_key,
    )
    record.status = "exported"
    record.external_reference = result.external_reference
    record.error_message = None
    await session.flush()
    return record


async def record_export_failure(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    journal_entry_id: UUID,
    idempotency_key: str,
    error_message: str,
) -> ERPExport:
    record = await get_or_create_export(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        journal_entry_id=journal_entry_id,
        idempotency_key=idempotency_key,
    )
    record.status = "failed"
    record.error_message = error_message[:2000]
    await session.flush()
    return record


async def list_export_records(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    status: str | None = None,
) -> list[ERPExport]:
    statement = select(ERPExport).where(
        ERPExport.buro_id == buro_id,
        ERPExport.mukellef_id == mukellef_id,
    )
    if status is not None:
        if status not in {"pending", "exported", "failed"}:
            raise ValueError("Geçersiz ERP aktarım durumu.")
        statement = statement.where(ERPExport.status == status)
    statement = statement.order_by(ERPExport.created_at.desc())
    return list((await session.scalars(statement)).all())


async def get_export_record(
    session: AsyncSession,
    *,
    export_id: UUID,
    buro_id: UUID,
    mukellef_id: UUID,
) -> ERPExport | None:
    return await session.scalar(
        select(ERPExport).where(
            ERPExport.id == export_id,
            ERPExport.buro_id == buro_id,
            ERPExport.mukellef_id == mukellef_id,
        )
    )
