from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import JournalEntry, JournalLine


@dataclass(frozen=True)
class TaxSummaryLine:
    category: str
    debit: Decimal
    credit: Decimal
    net: Decimal
    journal_entry_ids: tuple[UUID, ...]


async def summarize_approved_entries(
    session: AsyncSession,
    *,
    buro_id: UUID,
    mukellef_id: UUID,
    start_date: date,
    end_date: date,
    account_prefixes: dict[str, tuple[str, ...]],
) -> tuple[TaxSummaryLine, ...]:
    if start_date > end_date:
        raise ValueError("Başlangıç tarihi bitiş tarihinden sonra olamaz.")
    if not account_prefixes:
        raise ValueError("En az bir hesap kategorisi tanımlanmalıdır.")
    if any(not category.strip() for category in account_prefixes):
        raise ValueError("Vergi hesap kategorisi boş olamaz.")
    if any(not prefix.strip() for prefixes in account_prefixes.values() for prefix in prefixes):
        raise ValueError("Hesap kodu öneki boş olamaz.")

    rows = (
        await session.execute(
            select(
                JournalEntry.id,
                JournalLine.account_code,
                JournalLine.debit,
                JournalLine.credit,
            )
            .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
            .where(
                JournalEntry.buro_id == buro_id,
                JournalEntry.mukellef_id == mukellef_id,
                JournalEntry.entry_date >= start_date,
                JournalEntry.entry_date <= end_date,
                JournalEntry.status == "approved",
                JournalLine.buro_id == buro_id,
                JournalLine.mukellef_id == mukellef_id,
            )
        )
    ).all()

    result: list[TaxSummaryLine] = []
    for category, prefixes in account_prefixes.items():
        normalized_prefixes = tuple(prefix.strip() for prefix in prefixes)
        debit = Decimal("0")
        credit = Decimal("0")
        entry_ids: set[UUID] = set()
        for entry_id, account_code, line_debit, line_credit in rows:
            if account_code.startswith(normalized_prefixes):
                debit += line_debit
                credit += line_credit
                entry_ids.add(entry_id)
        result.append(
            TaxSummaryLine(
                category=category.strip(),
                debit=debit,
                credit=credit,
                net=debit - credit,
                journal_entry_ids=tuple(sorted(entry_ids, key=str)),
            )
        )
    return tuple(result)
