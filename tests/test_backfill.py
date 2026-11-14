"""Part 2c: geriye dönük fiş numaralandırma (backfill-fis-no).

Tüm testler geçici, sentetik veritabanlarında çalışır. Uygulama (apply) modu yalnızca bu test
veritabanlarında sınanır; canlı veritabanında çalıştırılmaz.
"""

import argparse
import asyncio
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import cli
from app.core import schema_check
from app.core.config import get_settings
from app.core.db import build_engine
from app.core.models import (
    AuditLog,
    Base,
    Buro,
    HesapDonemi,
    JournalEntry,
    JournalLine,
    Mukellef,
    NumaraSayaci,
)
from app.ledger import backfill as backfill_module
from app.ledger.backfill import BackfillError, apply_plan, build_plan

BURO, M1, M2 = uuid4(), uuid4(), uuid4()
USER = uuid4()
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


# --- Kurulum yardımcıları ---------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'bf.sqlite3'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(Buro(id=BURO, unvan="Büro"))
        s.add_all([
            Mukellef(id=M1, buro_id=BURO, vkn_tckn="1111111111", unvan="M1", mukellef_turu="kurumlar"),
            Mukellef(id=M2, buro_id=BURO, vkn_tckn="2222222222", unvan="M2", mukellef_turu="kurumlar"),
        ])
        await s.commit()
    yield factory, tmp_path / "bf.sqlite3"
    await engine.dispose()


async def add_period(factory, mukellef=M1, year=2026, status="open") -> UUID:
    pid = uuid4()
    async with factory() as s:
        s.add(HesapDonemi(id=pid, buro_id=BURO, mukellef_id=mukellef, start_date=date(year, 1, 1),
                          end_date=date(year, 12, 31), status=status))
        await s.commit()
    return pid


async def add_entry(factory, *, day: date, created: datetime, mukellef=M1, status="approved",
                    desc="Fiş", balanced=True, fis_no=None, hesap_donemi_id=None,
                    approved_audit: datetime | None = None) -> UUID:
    eid = uuid4()
    async with factory() as s:
        s.add(JournalEntry(id=eid, buro_id=BURO, mukellef_id=mukellef, entry_date=day,
                           description=desc, status=status, fis_no=fis_no,
                           hesap_donemi_id=hesap_donemi_id, created_at=created))
        s.add(JournalLine(buro_id=BURO, mukellef_id=mukellef, journal_entry_id=eid, line_order=1,
                          account_code="100", account_name="Kasa", debit=Decimal("10"), credit=Decimal("0")))
        s.add(JournalLine(buro_id=BURO, mukellef_id=mukellef, journal_entry_id=eid, line_order=2,
                          account_code="600", account_name="Satış", debit=Decimal("0"),
                          credit=Decimal("10" if balanced else "9")))
        if approved_audit:
            s.add(AuditLog(buro_id=BURO, user_id=USER, action="document.approve",
                           target_type="journal_entry", target_id=str(eid), created_at=approved_audit))
        await s.commit()
    return eid


async def plan_of(factory, **kw):
    async with factory() as s:
        plan = await build_plan(s, **kw)
        await s.rollback()
    return plan


def db_state(path: Path) -> list:
    c = sqlite3.connect(path)
    try:
        return [
            c.execute("select id, hesap_donemi_id, fis_no, approved_at, approved_by, reversal_of_id, "
                      "status, description, entry_date from yevmiye_fisleri order by id").fetchall(),
            c.execute("select count(*) from yevmiye_satirlari").fetchone(),
            c.execute("select * from numara_sayaclari order by id").fetchall(),
            c.execute("select count(*) from hesap_donemleri").fetchone(),
        ]
    finally:
        c.close()


async def do_apply(factory, fingerprint, **kw):
    async with factory() as s:
        try:
            plan = await apply_plan(s, expected_fingerprint=fingerprint, **kw)
            await s.commit()
            return plan
        except BaseException:
            await s.rollback()
            raise


# --- Plan ve sıralama -------------------------------------------------------


async def test_dry_run_orders_entries_and_writes_nothing(db) -> None:
    factory, path = db
    await add_period(factory)
    # Kasıtlı karışık giriş sırası
    e3 = await add_entry(factory, day=date(2026, 9, 10), created=T0, desc="10 Eylül")
    e1 = await add_entry(factory, day=date(2026, 9, 1), created=T0 + timedelta(hours=5), desc="1 Eylül geç")
    e0 = await add_entry(factory, day=date(2026, 9, 1), created=T0 + timedelta(hours=1), desc="1 Eylül erken")
    # Aynı gün + aynı created_at: onay kanıtı zamanı belirleyici
    e2b = await add_entry(factory, day=date(2026, 9, 5), created=T0, desc="5 Eylül B",
                          approved_audit=T0 + timedelta(days=5, hours=2))
    e2a = await add_entry(factory, day=date(2026, 9, 5), created=T0, desc="5 Eylül A",
                          approved_audit=T0 + timedelta(days=5, hours=1))
    await add_entry(factory, day=date(2026, 9, 2), created=T0, status="draft", desc="taslak")
    before = db_state(path)

    plan = await plan_of(factory)
    assert not plan.blocked and plan.eligible == 5
    order = [a.entry_id for a in plan.assignments]
    assert order == [e0, e1, e2a, e2b, e3]
    assert [a.fis_no for a in plan.assignments] == [1, 2, 3, 4, 5]
    assert db_state(path) == before  # dry-run hiçbir şey yazmadı
    report = "\n".join(plan.report_lines())
    assert "fiş no 1–5" in report and "onay kanıtı 2/5" in report
    assert plan.fingerprint == (await plan_of(factory)).fingerprint  # belirleyici


async def test_approval_evidence_only_from_audit_log(db) -> None:
    factory, _ = db
    await add_period(factory)
    with_audit = await add_entry(factory, day=date(2026, 9, 1), created=T0, approved_audit=T0 + timedelta(hours=3))
    without = await add_entry(factory, day=date(2026, 9, 2), created=T0)
    plan = await plan_of(factory)
    by_id = {a.entry_id: a for a in plan.assignments}
    assert by_id[with_audit].approved_by == USER and by_id[with_audit].approved_at is not None
    assert by_id[without].approved_at is None and by_id[without].approved_by is None  # uydurulmaz


# --- Bloklayan durumlar -----------------------------------------------------


async def test_missing_period_blocks_and_creates_no_period(db) -> None:
    factory, path = db
    await add_entry(factory, day=date(2026, 9, 1), created=T0)
    plan = await plan_of(factory)
    assert plan.blocked and [i.code for i in plan.issues] == ["donem_yok"]
    assert db_state(path)[3] == (0,)  # hesap dönemi oluşturulmadı
    with pytest.raises(BackfillError, match="Bloklayan"):
        await do_apply(factory, plan.fingerprint)


async def test_closed_and_overlapping_periods_block(db) -> None:
    factory, _ = db
    await add_period(factory, year=2025, status="closed")
    await add_entry(factory, day=date(2025, 6, 1), created=T0)
    assert [i.code for i in (await plan_of(factory)).issues] == ["donem_kapali"]

    await add_period(factory, mukellef=M2)
    async with factory() as s:  # M2 için 2026 ile çakışan ikinci dönem (veri hatası)
        s.add(HesapDonemi(buro_id=BURO, mukellef_id=M2, start_date=date(2026, 6, 1), end_date=date(2027, 5, 31)))
        await s.commit()
    await add_entry(factory, mukellef=M2, day=date(2026, 9, 1), created=T0)
    assert "donem_cakisiyor" in [i.code for i in (await plan_of(factory)).issues]


async def test_existing_numbers_are_never_changed(db) -> None:
    factory, path = db
    p26 = await add_period(factory)
    await add_entry(factory, day=date(2026, 9, 3), created=T0, fis_no=1, hesap_donemi_id=p26)
    await add_entry(factory, day=date(2026, 9, 1), created=T0)  # daha eski tarihli numarasız
    before = db_state(path)
    plan = await plan_of(factory)
    assert [i.code for i in plan.issues] == ["numaralandirma_baslamis"]
    assert plan.skipped_numbered == 1
    with pytest.raises(BackfillError):
        await do_apply(factory, plan.fingerprint)
    assert db_state(path) == before


async def test_other_scopes_numbered_entries_untouched(db) -> None:
    factory, path = db
    await add_period(factory)
    p27 = await add_period(factory, year=2027)
    numbered = await add_entry(factory, day=date(2027, 1, 5), created=T0, fis_no=7, hesap_donemi_id=p27)
    await add_entry(factory, day=date(2026, 9, 1), created=T0)
    plan = await plan_of(factory)
    assert not plan.blocked and len(plan.assignments) == 1
    await do_apply(factory, plan.fingerprint)
    row = sqlite3.connect(path).execute(
        "select fis_no from yevmiye_fisleri where id=?", (numbered.hex,)).fetchone()
    assert row == (7,)


async def test_unbalanced_entry_and_inconsistent_counter_block(db) -> None:
    factory, _ = db
    pid = await add_period(factory)
    await add_entry(factory, day=date(2026, 9, 1), created=T0, balanced=False)
    assert "dengesiz_fis" in [i.code for i in (await plan_of(factory)).issues]

    pid2 = await add_period(factory, mukellef=M2)
    async with factory() as s:
        s.add(NumaraSayaci(buro_id=BURO, mukellef_id=M2, hesap_donemi_id=pid2, sayac_turu="fis", son_numara=3))
        await s.commit()
    await add_entry(factory, mukellef=M2, day=date(2026, 9, 1), created=T0)
    assert "sayac_tutarsiz" in [i.code for i in (await plan_of(factory)).issues]
    assert pid  # kullanılmayan değişken uyarısı olmasın


async def test_reversal_link_from_audit_and_conflict_blocks(db) -> None:
    factory, _ = db
    await add_period(factory)
    original = await add_entry(factory, day=date(2026, 9, 1), created=T0, desc="Satış")
    reversal = await add_entry(factory, day=date(2026, 9, 1), created=T0 + timedelta(hours=1),
                               desc="Ters kayıt: Satış")
    async with factory() as s:
        s.add(AuditLog(buro_id=BURO, user_id=USER, action="ledger.reverse", target_type="journal_entry",
                       target_id=str(original), details={"reversal_entry_id": str(reversal)},
                       created_at=T0 + timedelta(hours=2)))
        await s.commit()
    plan = await plan_of(factory)
    link = {a.entry_id: a.reversal_of_id for a in plan.assignments}
    assert link[reversal] == original and link[original] is None

    other = await add_entry(factory, day=date(2026, 9, 2), created=T0, desc="Başka")
    async with factory() as s:  # aynı ters fiş için ikinci, çelişkili kanıt
        s.add(AuditLog(buro_id=BURO, user_id=USER, action="ledger.reverse", target_type="journal_entry",
                       target_id=str(other), details={"reversal_entry_id": str(reversal)}))
        await s.commit()
    assert "ters_kayit_celiskili" in [i.code for i in (await plan_of(factory)).issues]


# --- Uygulama, idempotency, bütünlük ----------------------------------------


async def test_apply_then_rerun_is_idempotent(db) -> None:
    factory, path = db
    pid = await add_period(factory)
    for d in (5, 1, 3):
        await add_entry(factory, day=date(2026, 9, d), created=T0, approved_audit=T0 + timedelta(days=d))
    plan = await plan_of(factory)
    await do_apply(factory, plan.fingerprint)

    rows = sqlite3.connect(path).execute(
        "select entry_date, fis_no, hesap_donemi_id is not null, approved_by is not null "
        "from yevmiye_fisleri order by fis_no").fetchall()
    assert [r[1] for r in rows] == [1, 2, 3] and [r[0] for r in rows] == ["2026-09-01", "2026-09-03", "2026-09-05"]
    assert all(r[2] and r[3] for r in rows)
    async with factory() as s:
        assert await s.scalar(select(NumaraSayaci.son_numara).where(NumaraSayaci.hesap_donemi_id == pid)) == 3

    after_first = db_state(path)
    rerun = await plan_of(factory)
    assert rerun.eligible == 0 and not rerun.assignments and rerun.skipped_numbered == 3
    assert "Yapılacak iş yok." in rerun.report_lines()
    await do_apply(factory, rerun.fingerprint)  # tekrar uygulamak hiçbir şeyi değiştirmez
    assert db_state(path) == after_first


async def test_stale_or_wrong_fingerprint_is_refused(db) -> None:
    factory, path = db
    await add_period(factory)
    await add_entry(factory, day=date(2026, 9, 1), created=T0)
    plan = await plan_of(factory)
    before = db_state(path)
    with pytest.raises(BackfillError, match="Plan değişti"):
        await do_apply(factory, "0000000000000000")
    # Dry-run ile uygulama arasında veri değişirse eski parmak izi geçersizdir
    await add_entry(factory, day=date(2026, 9, 2), created=T0)
    changed = db_state(path)
    with pytest.raises(BackfillError, match="Plan değişti"):
        await do_apply(factory, plan.fingerprint)
    assert db_state(path) == changed and changed != before


async def test_failed_verification_rolls_everything_back(db, monkeypatch) -> None:
    factory, path = db
    await add_period(factory)
    await add_entry(factory, day=date(2026, 9, 1), created=T0)
    plan = await plan_of(factory)
    before = db_state(path)
    monkeypatch.setattr(backfill_module, "verify", lambda *a: ["yapay doğrulama hatası"])
    with pytest.raises(BackfillError, match="yapay doğrulama hatası"):
        await do_apply(factory, plan.fingerprint)
    assert db_state(path) == before  # numara ve sayaç geri alındı


async def test_verify_detects_changed_existing_or_foreign_columns(db) -> None:
    factory, _ = db
    await add_period(factory)
    eid = await add_entry(factory, day=date(2026, 9, 1), created=T0)
    plan = await plan_of(factory)
    async with factory() as s:
        before = await backfill_module._snapshot(s)
    after = {"lines": before["lines"], "entries": {k: dict(v) for k, v in before["entries"].items()}}
    after["entries"][eid]["description"] = "değiştirildi"
    after["entries"][eid]["fis_no"] = 1
    assert any("Hedef dışı" in p for p in backfill_module.verify(before, after, plan))
    after["entries"][eid]["description"] = before["entries"][eid]["description"]
    after["lines"] = before["lines"] + 1
    assert any("satır" in p for p in backfill_module.verify(before, after, plan))


async def test_mukellef_filter(db) -> None:
    factory, _ = db
    await add_period(factory)
    await add_period(factory, mukellef=M2)
    await add_entry(factory, day=date(2026, 9, 1), created=T0)
    await add_entry(factory, mukellef=M2, day=date(2026, 9, 1), created=T0)
    plan = await plan_of(factory, mukellef_id=M2)
    assert {a.mukellef_id for a in plan.assignments} == {M2}


# --- CLI: şema, yedek, dry-run varsayılanı -----------------------------------


def _args(**kw) -> argparse.Namespace:
    base = {"mukellef": None, "uygula": False, "onay": None, "yedek_alindi": False}
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    path = tmp_path / "cli.sqlite3"
    url = f"sqlite+aiosqlite:///{path}"
    command.upgrade(schema_check.alembic_config(url), "head")
    engine = build_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "engine", engine)
    monkeypatch.setattr(cli, "session_factory", factory)
    monkeypatch.setattr(get_settings(), "database_url", url)
    monkeypatch.setattr(schema_check, "BACKUP_DIR", tmp_path / "yedek")

    async def seed():
        async with factory() as s:
            s.add(Buro(id=BURO, unvan="Büro"))
            s.add(Mukellef(id=M1, buro_id=BURO, vkn_tckn="1111111111", unvan="M1", mukellef_turu="kurumlar"))
            await s.commit()
        await add_period(factory)
        await add_entry(factory, day=date(2026, 9, 1), created=T0)

    asyncio.run(seed())
    yield factory, path, tmp_path
    asyncio.run(engine.dispose())


def test_cli_refuses_unmigrated_database(tmp_path, monkeypatch, capsys) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'eski.sqlite3'}"
    engine = build_engine(url)
    monkeypatch.setattr(cli, "engine", engine)
    with pytest.raises(SystemExit, match="migrate"):
        asyncio.run(cli.backfill_fis_no(_args()))
    asyncio.run(engine.dispose())


def test_cli_dry_run_is_default_and_writes_nothing(migrated, capsys) -> None:
    _, path, tmp = migrated
    before = db_state(path)
    assert asyncio.run(cli.backfill_fis_no(_args())) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "otomatik tutarlı yedek" in out and "--onay" in out
    assert db_state(path) == before
    assert not (tmp / "yedek").exists()  # dry-run yedek almaz (salt-okunur)


def test_cli_apply_requires_fingerprint_and_takes_backup(migrated, capsys) -> None:
    factory, path, tmp = migrated
    with pytest.raises(SystemExit, match="--onay"):
        asyncio.run(cli.backfill_fis_no(_args(uygula=True)))
    plan = asyncio.run(plan_of(factory))
    assert asyncio.run(cli.backfill_fis_no(_args(uygula=True, onay=plan.fingerprint))) == 0
    backups = list((tmp / "yedek").glob("*.pre-backfill.sqlite3"))
    assert len(backups) == 1
    assert sqlite3.connect(backups[0]).execute("select fis_no from yevmiye_fisleri").fetchall() == [(None,)]
    assert sqlite3.connect(path).execute("select fis_no from yevmiye_fisleri").fetchall() == [(1,)]
    log = sqlite3.connect(path).execute(
        "select count(*) from denetim_kayitlari where action='ledger.backfill_fis_no'").fetchone()
    assert log == (1,)


def test_cli_wrong_fingerprint_changes_nothing(migrated, capsys) -> None:
    _, path, _ = migrated
    before = db_state(path)
    assert asyncio.run(cli.backfill_fis_no(_args(uygula=True, onay="ffffffffffffffff"))) == 1
    assert "UYGULANMADI" in capsys.readouterr().out
    assert db_state(path) == before
