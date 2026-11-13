from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import AccountingPeriod, JournalEntry, JournalLine
from app.ledger.draft import (
    JournalEntryDraft,
    JournalLine as DraftJournalLine,
    LedgerValidationError,
    validate_balanced,
)
from app.ledger.reversal import reverse_entry_draft


async def list_entries(
    session: AsyncSession,
    *,
    buro_id,
    mukellef_id,
    status: str | None = None,
) -> list[JournalEntry]:
    statement = select(JournalEntry).where(
        JournalEntry.buro_id == buro_id,
        JournalEntry.mukellef_id == mukellef_id,
    )
    if status is not None:
        if status not in {"draft", "pending_review", "approved", "reversed"}:
            raise LedgerValidationError("Geçersiz yevmiye fişi durumu.")
        statement = statement.where(JournalEntry.status == status)
    statement = statement.order_by(JournalEntry.entry_date, JournalEntry.created_at)
    result = await session.execute(statement)
    return list(result.scalars().all())


async def get_entry_with_lines(
    session: AsyncSession,
    *,
    buro_id,
    mukellef_id,
    entry_id,
) -> tuple[JournalEntry, list[JournalLine]]:
    result = await session.execute(
        select(JournalEntry).where(
            JournalEntry.id == entry_id,
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise LedgerValidationError("Yevmiye fişi bulunamadı.")
    lines_result = await session.execute(
        select(JournalLine)
        .where(
            JournalLine.journal_entry_id == entry.id,
            JournalLine.buro_id == buro_id,
            JournalLine.mukellef_id == mukellef_id,
        )
        .order_by(JournalLine.line_order)
    )
    return entry, list(lines_result.scalars().all())


async def ensure_period_open(
    session: AsyncSession,
    *,
    mukellef_id,
    entry_date: date,
) -> None:
    result = await session.execute(
        select(AccountingPeriod.status).where(
            AccountingPeriod.mukellef_id == mukellef_id,
            AccountingPeriod.start_date <= entry_date,
            AccountingPeriod.end_date >= entry_date,
        )
    )
    status = result.scalar_one_or_none()
    if status == "locked":
        raise LedgerValidationError(
            f"{entry_date.isoformat()} tarihli muhasebe dönemi kilitli."
        )


async def persist_draft(
    session: AsyncSession,
    *,
    buro_id,
    mukellef_id,
    draft: JournalEntryDraft,
) -> JournalEntry:
    validate_balanced(draft)
    await ensure_period_open(
        session,
        mukellef_id=mukellef_id,
        entry_date=draft.entry_date,
    )
    entry = JournalEntry(
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        entry_date=draft.entry_date,
        description=draft.description,
        status="draft",
        source_document_id=draft.source_document_id,
        rule_name=draft.rule_name,
        rule_version=draft.rule_version,
        confidence=draft.confidence,
    )
    session.add(entry)
    await session.flush()
    for line_order, line in enumerate(draft.lines, start=1):
        session.add(
            JournalLine(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                journal_entry_id=entry.id,
                line_order=line_order,
                account_code=line.account_code,
                account_name=line.account_name,
                debit=line.debit,
                credit=line.credit,
            )
        )
    await session.flush()
    return entry


async def approve_entry(
    session: AsyncSession,
    *,
    buro_id,
    mukellef_id,
    entry_id,
) -> JournalEntry:
    result = await session.execute(
        select(JournalEntry).where(
            JournalEntry.id == entry_id,
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise LedgerValidationError("Yevmiye fişi bulunamadı.")
    if entry.status != "draft":
        raise LedgerValidationError(
            f"Yalnızca taslak fiş onaylanabilir; mevcut durum: {entry.status}."
        )

    lines_result = await session.execute(
        select(JournalLine).where(
            JournalLine.journal_entry_id == entry.id,
            JournalLine.buro_id == buro_id,
            JournalLine.mukellef_id == mukellef_id,
        )
    )
    lines = lines_result.scalars().all()
    if len(lines) < 2:
        raise LedgerValidationError("Yevmiye fişi en az iki satır içermelidir.")
    total_debit = sum((line.debit for line in lines), 0)
    total_credit = sum((line.credit for line in lines), 0)
    if total_debit != total_credit:
        raise LedgerValidationError(
            f"Yevmiye fişi dengede değil: borç={total_debit}, alacak={total_credit}."
        )
    entry.status = "approved"
    await session.flush()
    return entry


async def approve_entries(
    session: AsyncSession,
    *,
    buro_id,
    mukellef_id,
    entry_ids: list,
) -> list[JournalEntry]:
    if not entry_ids:
        raise LedgerValidationError("Onaylanacak fiş listesi boş olamaz.")
    unique_ids = list(dict.fromkeys(entry_ids))
    if len(unique_ids) != len(entry_ids):
        raise LedgerValidationError("Onay listesinde aynı fiş birden fazla kez bulunamaz.")
    result = await session.execute(
        select(JournalEntry).where(
            JournalEntry.id.in_(unique_ids),
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
        )
    )
    entries = list(result.scalars().all())
    if len(entries) != len(unique_ids):
        raise LedgerValidationError("Onaylanacak fişlerden biri bulunamadı.")
    if any(entry.status != "draft" for entry in entries):
        raise LedgerValidationError("Yalnızca taslak fişler toplu onaylanabilir.")

    lines_result = await session.execute(
        select(JournalLine).where(
            JournalLine.journal_entry_id.in_(unique_ids),
            JournalLine.buro_id == buro_id,
            JournalLine.mukellef_id == mukellef_id,
        )
    )
    lines_by_entry: dict = {}
    for line in lines_result.scalars().all():
        lines_by_entry.setdefault(line.journal_entry_id, []).append(line)
    for entry in entries:
        lines = lines_by_entry.get(entry.id, [])
        if len(lines) < 2:
            raise LedgerValidationError("Yevmiye fişi en az iki satır içermelidir.")
        total_debit = sum((line.debit for line in lines), 0)
        total_credit = sum((line.credit for line in lines), 0)
        if total_debit != total_credit:
            raise LedgerValidationError(
                f"Yevmiye fişi dengede değil: borç={total_debit}, alacak={total_credit}."
            )

    for entry in entries:
        entry.status = "approved"
    await session.flush()
    return entries


async def persist_reversal(
    session: AsyncSession,
    *,
    buro_id,
    mukellef_id,
    entry_id,
) -> JournalEntry:
    result = await session.execute(
        select(JournalEntry).where(
            JournalEntry.id == entry_id,
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise LedgerValidationError("Yevmiye fişi bulunamadı.")
    if entry.status != "approved":
        raise LedgerValidationError("Yalnızca onaylanmış fiş terslenebilir.")
    if (entry.rule_name or "").endswith("reversal") or entry.description.startswith("Ters kayıt: "):
        raise LedgerValidationError("Ters kayıt fişi tekrar terslenemez.")
    # Aynı fişin iki kez terslenmesini engelle (çift ters kayıt mizanı bozar).
    duplicate = await session.scalar(
        select(JournalEntry.id).where(
            JournalEntry.buro_id == buro_id,
            JournalEntry.mukellef_id == mukellef_id,
            JournalEntry.description == f"Ters kayıt: {entry.description}",
            JournalEntry.entry_date == entry.entry_date,
            (
                JournalEntry.source_document_id.is_(None)
                if entry.source_document_id is None
                else JournalEntry.source_document_id == entry.source_document_id
            ),
            JournalEntry.status.in_(("draft", "pending_review", "approved")),
        )
    )
    if duplicate is not None:
        raise LedgerValidationError("Bu fiş için zaten ters kayıt oluşturulmuş.")

    lines_result = await session.execute(
        select(JournalLine).where(
            JournalLine.journal_entry_id == entry.id,
            JournalLine.buro_id == buro_id,
            JournalLine.mukellef_id == mukellef_id,
        )
    )
    source_lines = lines_result.scalars().all()
    source_draft = JournalEntryDraft(
        entry_date=entry.entry_date,
        description=entry.description,
        lines=tuple(
            DraftJournalLine(
                account_code=line.account_code,
                account_name=line.account_name,
                debit=line.debit,
                credit=line.credit,
            )
            for line in source_lines
        ),
        source_document_id=entry.source_document_id,
        rule_name=entry.rule_name,
        rule_version=entry.rule_version,
        confidence=entry.confidence,
    )
    reversal = reverse_entry_draft(source_draft)
    return await persist_draft(
        session,
        buro_id=buro_id,
        mukellef_id=mukellef_id,
        draft=reversal,
    )
