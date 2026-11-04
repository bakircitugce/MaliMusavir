from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import AccountingPeriod, Base, JournalLine as JournalLineModel
from app.ledger import (
    JournalEntryDraft,
    JournalLine,
    LedgerValidationError,
    approve_entry,
    approve_entries,
    ensure_period_open,
    persist_reversal,
    list_entries,
    get_entry_with_lines,
    validate_balanced,
)
from app.ledger.service import persist_draft


def test_balanced_journal_entry_is_accepted() -> None:
    entry = JournalEntryDraft(
        entry_date=date(2026, 11, 3),
        description="Örnek alış faturası",
        lines=(
            JournalLine("153.01.001", "Ticari Mallar", debit=Decimal("100.00")),
            JournalLine("191.03.001", "İndirilecek KDV", debit=Decimal("20.00")),
            JournalLine("320.01.001", "Örnek Tedarikçi", credit=Decimal("120.00")),
        ),
        rule_name="purchase_invoice",
        rule_version="1",
        confidence=Decimal("0.95"),
    )

    assert validate_balanced(entry) is entry
    assert entry.total_debit == Decimal("120.00")
    assert entry.total_credit == Decimal("120.00")


def test_unbalanced_journal_entry_is_rejected() -> None:
    entry = JournalEntryDraft(
        entry_date=date(2026, 11, 3),
        description="Dengesiz taslak",
        lines=(
            JournalLine("100.01.001", "Kasa", debit=Decimal("100.00")),
            JournalLine("600.03.001", "Yurtiçi Satış", credit=Decimal("90.00")),
        ),
    )

    with pytest.raises(LedgerValidationError, match="dengede değil"):
        validate_balanced(entry)


@pytest.mark.parametrize(
    "line",
    (
        JournalLine("100.01.001", "Kasa", debit=Decimal("1.00")),
        JournalLine("600.03.001", "Satış", credit=Decimal("1.00")),
    ),
)
def test_journal_line_requires_one_side(line: JournalLine) -> None:
    assert line.debit or line.credit


def test_journal_line_rejects_both_sides() -> None:
    with pytest.raises(LedgerValidationError, match="hem borç hem alacak"):
        JournalLine("100.01.001", "Kasa", debit=Decimal("1.00"), credit=Decimal("1.00"))


@pytest.mark.asyncio
async def test_persist_draft_writes_entry_and_lines() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    draft = JournalEntryDraft(
        entry_date=date(2026, 11, 3),
        description="Kalıcı taslak",
        lines=(
            JournalLine("100.01.001", "Kasa", debit=Decimal("120.00")),
            JournalLine("600.03.001", "Satış", credit=Decimal("120.00")),
        ),
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        entry = await persist_draft(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            draft=draft,
        )
        await session.commit()

        assert entry.status == "draft"
        lines = (
            await session.execute(
                select(JournalLineModel).where(JournalLineModel.journal_entry_id == entry.id)
            )
        ).scalars().all()
        assert len(lines) == 2
        assert all(line.buro_id == buro_id for line in lines)
    await engine.dispose()


@pytest.mark.asyncio
async def test_persist_draft_rejects_locked_period() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        session.add(
            AccountingPeriod(
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                start_date=date(2026, 11, 1),
                end_date=date(2026, 11, 30),
                status="locked",
            )
        )
        await session.commit()
        draft = JournalEntryDraft(
            entry_date=date(2026, 11, 3),
            description="Kilitli dönem fişi",
            lines=(
                JournalLine("100.01.001", "Kasa", debit=Decimal("10.00")),
                JournalLine("600.03.001", "Satış", credit=Decimal("10.00")),
            ),
        )

        with pytest.raises(LedgerValidationError, match="dönemi kilitli"):
            await persist_draft(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                draft=draft,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_approve_entry_changes_only_tenant_scoped_draft() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        draft = JournalEntryDraft(
            entry_date=date(2026, 11, 3),
            description="Onaylanacak fiş",
            lines=(
                JournalLine("100.01.001", "Kasa", debit=Decimal("10.00")),
                JournalLine("600.03.001", "Satış", credit=Decimal("10.00")),
            ),
        )
        entry = await persist_draft(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            draft=draft,
        )
        approved = await approve_entry(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_id=entry.id,
        )
        assert approved.status == "approved"

        with pytest.raises(LedgerValidationError, match="Yalnızca taslak"):
            await approve_entry(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                entry_id=entry.id,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_persist_reversal_keeps_approved_entry_and_creates_draft() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        source = await persist_draft(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            draft=JournalEntryDraft(
                entry_date=date(2026, 11, 3),
                description="Onaylı fiş",
                lines=(
                    JournalLine("100.01.001", "Kasa", debit=Decimal("10.00")),
                    JournalLine("600.03.001", "Satış", credit=Decimal("10.00")),
                ),
            ),
        )
        await approve_entry(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_id=source.id,
        )
        reversal = await persist_reversal(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_id=source.id,
        )

        assert source.status == "approved"
        assert reversal.status == "draft"
        assert reversal.description == "Ters kayıt: Onaylı fiş"
        assert reversal.rule_name == "reversal"
        assert reversal.id != source.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_entries_filters_by_tenant_and_status() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    other_buro_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        for current_buro_id, description in (
            (buro_id, "Bu mükellefin taslağı"),
            (other_buro_id, "Başka büronun taslağı"),
        ):
            await persist_draft(
                session,
                buro_id=current_buro_id,
                mukellef_id=mukellef_id,
                draft=JournalEntryDraft(
                    entry_date=date(2026, 11, 3),
                    description=description,
                    lines=(
                        JournalLine("100.01.001", "Kasa", debit=Decimal("10.00")),
                        JournalLine("600.03.001", "Satış", credit=Decimal("10.00")),
                    ),
                ),
            )
        entries = await list_entries(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            status="draft",
        )
        assert [entry.description for entry in entries] == ["Bu mükellefin taslağı"]
        with pytest.raises(LedgerValidationError, match="Geçersiz"):
            await list_entries(
                session,
                buro_id=buro_id,
                mukellef_id=mukellef_id,
                status="invalid",
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_entry_with_lines_returns_ordered_tenant_scoped_lines() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        entry = await persist_draft(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            draft=JournalEntryDraft(
                entry_date=date(2026, 11, 3),
                description="Detay fişi",
                lines=(
                    JournalLine("100.01.001", "Kasa", debit=Decimal("10.00")),
                    JournalLine("600.03.001", "Satış", credit=Decimal("10.00")),
                ),
            ),
        )
        loaded_entry, lines = await get_entry_with_lines(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_id=entry.id,
        )
        assert loaded_entry.id == entry.id
        assert [line.line_order for line in lines] == [1, 2]
        assert [line.account_code for line in lines] == ["100.01.001", "600.03.001"]

        with pytest.raises(LedgerValidationError, match="bulunamadı"):
            await get_entry_with_lines(
                session,
                buro_id=uuid4(),
                mukellef_id=mukellef_id,
                entry_id=entry.id,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_approve_entries_approves_tenant_scoped_drafts() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    buro_id = uuid4()
    mukellef_id = uuid4()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        entries = []
        for index in range(2):
            entries.append(
                await persist_draft(
                    session,
                    buro_id=buro_id,
                    mukellef_id=mukellef_id,
                    draft=JournalEntryDraft(
                        entry_date=date(2026, 11, 3),
                        description=f"Toplu fiş {index}",
                        lines=(
                            JournalLine("100.01.001", "Kasa", debit=Decimal("10.00")),
                            JournalLine("600.03.001", "Satış", credit=Decimal("10.00")),
                        ),
                    ),
                )
            )
        approved = await approve_entries(
            session,
            buro_id=buro_id,
            mukellef_id=mukellef_id,
            entry_ids=[entry.id for entry in entries],
        )
        assert len(approved) == 2
        assert all(entry.status == "approved" for entry in approved)
    await engine.dispose()


@pytest.mark.asyncio
async def test_approve_entries_rejects_empty_and_duplicate_ids() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        with pytest.raises(LedgerValidationError, match="boş"):
            await approve_entries(
                session,
                buro_id=uuid4(),
                mukellef_id=uuid4(),
                entry_ids=[],
            )
        with pytest.raises(LedgerValidationError, match="birden fazla"):
            duplicate_id = uuid4()
            await approve_entries(
                session,
                buro_id=uuid4(),
                mukellef_id=uuid4(),
                entry_ids=[duplicate_id, duplicate_id],
            )
    await engine.dispose()
