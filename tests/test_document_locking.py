"""D1a: belge kilidi, çakışma tespiti ve taslağın yerinde güncellenmesi.

Eşzamanlılık testleri, test paketinin modundan bağımsız olarak kendi dosya SQLite motorlarını
kurar:
- IMMEDIATE: `build_engine` (üretim yapılandırması).
- Gerçek DEFERRED: olay dinleyicisi olmayan `create_async_engine` (sürücü varsayılanı).
PostgreSQL bu ortamda yoktur; PostgreSQL'e özgü testler yalnızca SQL derleme düzeyindedir.
"""

import asyncio
import hashlib
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import build_engine, configure_sqlite_transactions
from app.core.locks import KilitGarantisiYok, KilitHatasi, KilitlenecekKayitYok, lock_document
from app.core.models import (
    AccountingPeriod,
    AuditLog,
    Base,
    Belge,
    Buro,
    JournalEntry,
    JournalLine,
    Mukellef,
)
from app.documents.extractor import _ubl_metadata
from app.documents.ubl import parse_ubl_invoice
from app.ledger.draft import JournalEntryDraft
from app.ledger.draft import JournalLine as DraftLine
from app.ledger.posting import (
    IslemSahipligiHatasi,
    document_entries,
    is_reversal,
    line_fingerprint,
    post_document,
)
from app.ledger.service import persist_draft
from tests.test_posting import SUPPLIER_VKN, _upload_and_process, office, ubl  # noqa: F401

MUKELLEF_VKN = "1111111111"
WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE")


# --- yardımcılar --------------------------------------------------------------------------


def _invoice_meta(invoice_id: str, customer_vkn: str, base: str = "1000.00") -> dict:
    vat = f"{float(base) * 0.2:.2f}"
    payable = f"{float(base) * 1.2:.2f}"
    content = ubl(invoice_id=invoice_id, supplier_vkn=MUKELLEF_VKN, customer_vkn=customer_vkn,
                  base=base, vat=vat, payable=payable)
    return {"invoice": _ubl_metadata(parse_ubl_invoice(content))}


async def _create_schema(engine) -> async_sessionmaker:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(factory, documents: int = 1) -> tuple:
    buro, muk = uuid4(), uuid4()
    doc_ids = [uuid4() for _ in range(documents)]
    async with factory() as s:
        s.add(Buro(id=buro, unvan="B"))
        s.add(Mukellef(id=muk, buro_id=buro, vkn_tckn=MUKELLEF_VKN, unvan="M",
                       mukellef_turu="kurumlar"))
        for i, doc_id in enumerate(doc_ids):
            s.add(Belge(
                id=doc_id, buro_id=buro, mukellef_id=muk, original_filename=f"f{i}.xml",
                content_type="application/xml", size_bytes=1, sha256=f"{i:064d}",
                storage_key=f"k/{doc_id}", document_type="ubl_xml",
                extraction_status="completed",
                extraction_metadata=_invoice_meta(f"SAT{i}", f"{3000000000 + i}"),
            ))
        await s.commit()
    return buro, muk, doc_ids


async def _load(s, doc_id) -> Belge:
    """Belgeyi okur ve okuma transaction'ını kapatır (post_document commit=True için ön koşul)."""
    doc = await s.get(Belge, doc_id)
    await s.commit()
    return doc


async def _process(factory, doc_id, **kwargs):
    """Yeni oturum, belgeyi oku, fiş üret (fonksiyon kendi transaction'ının sahibi)."""
    async with factory() as s:
        doc = await _load(s, doc_id)
        return await post_document(s, doc, **kwargs)


async def _active(factory, doc_id) -> list[JournalEntry]:
    async with factory() as s:
        doc = await s.get(Belge, doc_id)
        return list((await document_entries(s, doc)).active)


async def _lines(factory, entry_id) -> list[tuple]:
    async with factory() as s:
        rows = await s.scalars(select(JournalLine).where(JournalLine.journal_entry_id == entry_id)
                               .order_by(JournalLine.line_order))
        return [(r.account_code, r.account_name, r.debit, r.credit) for r in rows]


async def _audit_count(factory, action: str) -> int:
    async with factory() as s:
        return await s.scalar(select(func.count()).select_from(AuditLog)
                              .where(AuditLog.action == action))


def _db_checksum(path: Path) -> tuple[str, str]:
    """(mantıksal döküm özeti, dosya bayt özeti)."""
    conn = sqlite3.connect(path)
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    return (hashlib.sha256(dump.encode()).hexdigest(),
            hashlib.sha256(path.read_bytes()).hexdigest())


class SqlRecorder:
    def __init__(self, engine) -> None:
        self.engine = engine.sync_engine
        self.statements: list[str] = []
        self.commits = 0

    def _on_sql(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self.statements.append(" ".join(statement.split()))

    def _on_commit(self, conn) -> None:
        self.commits += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on_sql)
        event.listen(self.engine, "commit", self._on_commit)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self.engine, "before_cursor_execute", self._on_sql)
        event.remove(self.engine, "commit", self._on_commit)

    @property
    def writes(self) -> list[str]:
        return [s for s in self.statements if s.upper().startswith(WRITE_PREFIXES)]


@pytest.fixture
async def immediate(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'immediate.sqlite3'}")
    factory = await _create_schema(engine)
    yield engine, factory, tmp_path / "immediate.sqlite3"
    await engine.dispose()


# --- kilit katmanı -------------------------------------------------------------------------


async def test_sqlite_immediate_lock_is_taken_by_first_sql_and_not_interrupted(immediate) -> None:
    engine, factory, _ = immediate
    _, _, (doc_id,) = await _seed(factory)
    async with factory() as s:
        doc = await _load(s, doc_id)
        with SqlRecorder(engine) as rec:
            result = await post_document(s, doc)
    assert result.status in ("draft_ready", "needs_review") and result.entry_id
    # İlk SQL komutundan önce yazma kilidi alınır; ikinci BEGIN yoktur.
    assert rec.statements[0] == "BEGIN IMMEDIATE"
    assert sum(1 for s in rec.statements if s.upper().startswith("BEGIN")) == 1
    # Kilit sorgusu ile son yazma arasında transaction kesilmez: tek commit, en sonda.
    assert rec.commits == 1
    # SQLite lehçesi FOR UPDATE yazmaz; kilit BEGIN IMMEDIATE'ten gelir.
    assert not any("FOR UPDATE" in s.upper() for s in rec.statements)


def test_lock_query_renders_for_update_only_on_postgresql() -> None:
    """PostgreSQL sunucusu yok; yalnızca üretilen SQL doğrulanır (derleme testi)."""
    stmt = select(Belge).where(Belge.id == uuid4()).with_for_update()
    assert "FOR UPDATE" in str(stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in str(stmt.compile(dialect=sqlite.dialect()))


async def test_real_deferred_sqlite_is_rejected_and_database_unchanged(tmp_path) -> None:
    path = tmp_path / "deferred.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")  # olaysız: gerçek DEFERRED
    factory = await _create_schema(engine)
    _, _, (doc_id,) = await _seed(factory)
    await engine.dispose()
    before = _db_checksum(path)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with pytest.raises(KilitGarantisiYok):
        await _process(factory, doc_id)
    await engine.dispose()

    assert _db_checksum(path) == before


async def test_lock_requires_open_transaction(immediate) -> None:
    _, factory, _ = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    async with factory() as s:
        assert not s.in_transaction()
        with pytest.raises(KilitHatasi) as exc:
            await lock_document(s, buro_id=buro, mukellef_id=muk, document_id=doc_id)
        assert not isinstance(exc.value, KilitGarantisiYok)


async def test_lock_is_tenant_scoped(immediate) -> None:
    _, factory, _ = immediate
    buro, _, (doc_id,) = await _seed(factory)
    async with factory() as s:
        await s.begin()
        with pytest.raises(KilitlenecekKayitYok):
            await lock_document(s, buro_id=buro, mukellef_id=uuid4(), document_id=doc_id)


async def test_lock_rereads_object_loaded_before_lock(immediate) -> None:
    _, factory, _ = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    async with factory() as a:
        stale = await a.get(Belge, doc_id)
        await a.commit()  # expire_on_commit=False: nesne eski değerleriyle kalır
        async with factory() as b:
            await b.execute(update(Belge).where(Belge.id == doc_id)
                            .values(review_status="needs_review", review_note="b"))
            await b.commit()
        assert stale.review_note is None
        await a.begin()
        locked = await lock_document(a, buro_id=buro, mukellef_id=muk, document_id=doc_id)
        assert locked is stale
        assert locked.review_note == "b" and locked.review_status == "needs_review"


# --- eşzamanlılık (dosya SQLite, IMMEDIATE) -------------------------------------------------


async def test_concurrent_processing_of_same_document_creates_exactly_one_entry(immediate) -> None:
    _, factory, _ = immediate
    _, _, (doc_id,) = await _seed(factory)
    results = await asyncio.gather(*(_process(factory, doc_id) for _ in range(100)),
                                   return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == []
    active = await _active(factory, doc_id)
    assert len(active) == 1
    assert {r.entry_id for r in results} == {str(active[0].id)}
    assert await _audit_count(factory, "entry.draft_regenerated") == 0


async def test_concurrent_processing_of_different_documents(immediate) -> None:
    _, factory, _ = immediate
    _, _, doc_ids = await _seed(factory, documents=50)
    results = await asyncio.gather(*(_process(factory, d) for d in doc_ids for _ in range(2)),
                                   return_exceptions=True)
    assert [r for r in results if isinstance(r, BaseException)] == []
    for doc_id in doc_ids:
        assert len(await _active(factory, doc_id)) == 1
    async with factory() as s:
        assert await s.scalar(select(func.count()).select_from(JournalEntry)) == 50


# --- yerinde güncelleme ---------------------------------------------------------------------


async def test_same_content_refresh_writes_nothing(immediate) -> None:
    engine, factory, _ = immediate
    _, _, (doc_id,) = await _seed(factory)
    first = await _process(factory, doc_id)
    async with factory() as session:
        doc = await _load(session, doc_id)
        with SqlRecorder(engine) as rec:
            second = await post_document(session, doc)
        assert not session.in_transaction()
    assert second.entry_id == first.entry_id
    assert rec.writes == []
    assert rec.commits == 0
    assert await _audit_count(factory, "entry.draft_regenerated") == 0


async def test_changed_content_updates_same_entry_and_audits_fingerprints(immediate) -> None:
    _, factory, _ = immediate
    _, _, (doc_id,) = await _seed(factory)
    first = await _process(factory, doc_id)
    (entry,) = await _active(factory, doc_id)
    old_lines = await _lines(factory, entry.id)

    async with factory() as s:
        await s.execute(update(Belge).where(Belge.id == doc_id)
                        .values(extraction_metadata=_invoice_meta("SAT0", "3000000000", "2000.00")))
        await s.commit()
    second = await _process(factory, doc_id)

    (updated,) = await _active(factory, doc_id)
    new_lines = await _lines(factory, updated.id)
    assert second.entry_id == first.entry_id == str(entry.id)
    assert updated.id == entry.id
    assert updated.created_at == entry.created_at
    assert new_lines != old_lines
    async with factory() as s:
        assert await s.scalar(select(func.count()).select_from(JournalEntry)) == 1
        logs = (await s.scalars(select(AuditLog)
                                .where(AuditLog.action == "entry.draft_regenerated"))).all()
    assert len(logs) == 1
    details = logs[0].details
    assert logs[0].target_id == str(entry.id)
    assert details["old_fingerprint"] == line_fingerprint(entry.entry_date, old_lines)
    assert details["new_fingerprint"] == line_fingerprint(updated.entry_date, new_lines)
    assert details["old_fingerprint"] != details["new_fingerprint"]


async def test_validation_failure_keeps_old_draft(immediate) -> None:
    _, factory, _ = immediate
    _, muk, (doc_id,) = await _seed(factory)
    first = await _process(factory, doc_id)
    (entry,) = await _active(factory, doc_id)
    old_lines = await _lines(factory, entry.id)

    async with factory() as s:
        s.add(AccountingPeriod(buro_id=(await s.get(Mukellef, muk)).buro_id, mukellef_id=muk,
                               start_date=date(2026, 9, 1), end_date=date(2026, 9, 30),
                               status="locked"))
        await s.execute(update(Belge).where(Belge.id == doc_id)
                        .values(extraction_metadata=_invoice_meta("SAT0", "3000000000", "2000.00")))
        await s.commit()
    result = await _process(factory, doc_id)

    assert result.status == "needs_review"
    assert result.entry_id is None and result.stale_entry_id == first.entry_id
    (kept,) = await _active(factory, doc_id)
    assert kept.id == entry.id and kept.status == "draft" and kept.created_at == entry.created_at
    assert await _lines(factory, kept.id) == old_lines
    async with factory() as s:
        doc = await s.get(Belge, doc_id)
    assert doc.review_status == "needs_review"
    assert doc.extraction_metadata["posting"]["stale_entry_id"] == str(entry.id)
    assert await _audit_count(factory, "entry.draft_regenerated") == 0


async def test_pending_review_entry_is_not_modified(immediate) -> None:
    _, factory, _ = immediate
    _, _, (doc_id,) = await _seed(factory)
    await _process(factory, doc_id)
    (entry,) = await _active(factory, doc_id)
    async with factory() as s:
        await s.execute(update(JournalEntry).where(JournalEntry.id == entry.id)
                        .values(status="pending_review"))
        await s.execute(update(Belge).where(Belge.id == doc_id)
                        .values(extraction_metadata=_invoice_meta("SAT0", "3000000000", "2000.00")))
        await s.commit()
    old_lines = await _lines(factory, entry.id)
    result = await _process(factory, doc_id)
    assert result.status == "skipped"
    assert await _lines(factory, entry.id) == old_lines


# --- çakışma ----------------------------------------------------------------------------------


async def _add_duplicate_drafts(factory, buro, muk, doc_id, count: int = 2) -> None:
    async with factory() as s:
        for _ in range(count):
            await persist_draft(s, buro_id=buro, mukellef_id=muk, draft=JournalEntryDraft(
                entry_date=date(2026, 9, 15), description="çift",
                lines=(DraftLine("120", "Alıcılar", debit=Decimal("100.00")),
                       DraftLine("600", "Satış", credit=Decimal("100.00"))),
                source_document_id=doc_id, rule_name="sales_invoice", rule_version="1",
            ))
        await s.commit()


async def test_conflict_writes_nothing(immediate) -> None:
    engine, factory, path = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    await _add_duplicate_drafts(factory, buro, muk, doc_id)
    before = _db_checksum(path)

    async with factory() as session:
        doc = await _load(session, doc_id)
        with SqlRecorder(engine) as rec:
            result = await post_document(session, doc)
        assert not session.in_transaction()
    assert result.status == "conflict" and result.entry_id is None
    assert rec.writes == [] and rec.commits == 0
    assert _db_checksum(path)[0] == before[0]
    assert len(await _active(factory, doc_id)) == 2


async def test_document_entries_separates_reversals_and_orders_deterministically(immediate) -> None:
    _, factory, _ = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    await _add_duplicate_drafts(factory, buro, muk, doc_id, count=3)
    async with factory() as s:
        same_time = (await s.scalars(select(JournalEntry.created_at))).first()
        await s.execute(update(JournalEntry).values(created_at=same_time))
        reversal = JournalEntry(buro_id=buro, mukellef_id=muk, entry_date=date(2026, 9, 20),
                                description="Ters kayıt", status="approved",
                                source_document_id=doc_id, rule_name="sales_invoice.reversal")
        s.add(reversal)
        await s.commit()
        doc = await s.get(Belge, doc_id)
        first = await document_entries(s, doc)
        second = await document_entries(s, doc)
    assert [e.id for e in first.active] == [e.id for e in second.active]
    assert [str(e.id) for e in first.active] == sorted(str(e.id) for e in first.active)
    assert len(first.active) == 3 and first.has_conflict
    assert [e.id for e in first.reversals] == [reversal.id]
    assert is_reversal(reversal)


# --- transaction denetimi --------------------------------------------------------------------


async def test_commit_false_leaves_transaction_to_caller(immediate) -> None:
    engine, factory, _ = immediate
    _, _, (doc_id,) = await _seed(factory)
    with SqlRecorder(engine) as rec:
        async with factory() as s:
            doc = await s.get(Belge, doc_id)
            result = await post_document(s, doc, commit=False)
            assert result.entry_id and s.in_transaction()
            await s.rollback()
    assert rec.commits == 0
    assert await _active(factory, doc_id) == []


# --- API: güncel olmayan taslak onaylanamaz ---------------------------------------------------

async def test_stale_draft_cannot_be_approved_via_api(api_client, office, db_factory) -> None:  # noqa: F811
    doc_id = await _upload_and_process(
        api_client, office,
        ubl(invoice_id="ALIS1", supplier_vkn=SUPPLIER_VKN, customer_vkn=MUKELLEF_VKN), "a.xml",
    )
    async with db_factory() as s:
        mukellef = await s.get(Mukellef, office["m"])
        s.add(AccountingPeriod(buro_id=mukellef.buro_id, mukellef_id=office["m"],
                               start_date=date(2026, 9, 1), end_date=date(2026, 9, 30),
                               status="locked"))
        await s.commit()
    headers = office["personel"].headers(office["m"])
    assert (await api_client.post(f"/documents/{doc_id}/extract", headers=headers)).status_code == 200
    res = await api_client.post(f"/documents/{doc_id}/approve",
                                headers=office["musavir"].headers(office["m"]))
    assert res.status_code == 409
    assert "güncel değil" in res.json()["detail"]


# --- transaction sahipliği ve kilidin bırakılması ----------------------------------------------


async def _try_write(path: Path, timeout: float = 0.3) -> bool:
    """Ayrı bir bağlantıdan kısa bekleme süresiyle yazmayı dener (kilit tutuluyor mu?)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": timeout})
    configure_sqlite_transactions(engine)
    try:
        async with async_sessionmaker(engine)() as s:
            await s.execute(update(Buro).values(unvan="yazma-denemesi"))
            await s.commit()
        return True
    except OperationalError:
        return False
    finally:
        await engine.dispose()


async def _set_entry_status(factory, doc_id, status: str) -> None:
    (entry,) = await _active(factory, doc_id)
    async with factory() as s:
        await s.execute(update(JournalEntry).where(JournalEntry.id == entry.id).values(status=status))
        await s.commit()


async def _change_content(factory, doc_id) -> None:
    async with factory() as s:
        await s.execute(update(Belge).where(Belge.id == doc_id)
                        .values(extraction_metadata=_invoice_meta("SAT0", "3000000000", "2000.00")))
        await s.commit()


async def _lock_september(factory, buro, muk) -> None:
    async with factory() as s:
        s.add(AccountingPeriod(buro_id=buro, mukellef_id=muk, start_date=date(2026, 9, 1),
                               end_date=date(2026, 9, 30), status="locked"))
        await s.commit()


async def _scenario(name: str, factory, buro, muk, doc_id) -> str:
    """Senaryonun ön durumunu kurar; beklenen sonuç durumunu döner."""
    if name == "yeni_fis":
        return "draft_ready"
    await _process(factory, doc_id)
    if name == "ayni_icerik":
        return "draft_ready"
    if name == "guncelleme":
        await _change_content(factory, doc_id)
        return "draft_ready"
    if name == "onayli":
        await _set_entry_status(factory, doc_id, "approved")
        return "skipped"
    if name == "pending_review":
        await _set_entry_status(factory, doc_id, "pending_review")
        return "skipped"
    if name == "cakisma":
        await _add_duplicate_drafts(factory, buro, muk, doc_id, count=1)
        return "conflict"
    if name == "dogrulama_hatasi":
        await _lock_september(factory, buro, muk)
        await _change_content(factory, doc_id)
        return "needs_review"
    if name == "kural_yok":
        async with factory() as s:
            await s.execute(update(Belge).where(Belge.id == doc_id).values(extraction_metadata={}))
            await s.commit()
        return "needs_review"
    if name == "okunamadi":
        async with factory() as s:
            await s.execute(update(Belge).where(Belge.id == doc_id)
                            .values(extraction_status="failed"))
            await s.commit()
        return "needs_review"
    raise AssertionError(name)


ALL_OUTCOMES = ["yeni_fis", "guncelleme", "ayni_icerik", "onayli", "pending_review", "cakisma",
                "dogrulama_hatasi", "kural_yok", "okunamadi"]


@pytest.mark.parametrize("name", ALL_OUTCOMES)
async def test_owner_mode_closes_transaction_and_releases_lock_on_every_path(immediate, name) -> None:
    _, factory, path = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    expected = await _scenario(name, factory, buro, muk, doc_id)
    async with factory() as s:
        doc = await _load(s, doc_id)
        result = await post_document(s, doc)
        # Oturum hâlâ açıkken: transaction kapalı, kilit bırakılmış, başka bağlantı yazabilir.
        assert result.status == expected
        assert not s.in_transaction()
        assert await _try_write(path)


async def test_owner_mode_error_rolls_back_releases_lock_and_session_is_reusable(
    immediate, monkeypatch
) -> None:
    _, factory, path = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    await _scenario("guncelleme", factory, buro, muk, doc_id)
    (entry,) = await _active(factory, doc_id)
    old_lines = await _lines(factory, entry.id)

    async def failing_record(*args, **kwargs):
        raise RuntimeError("denetim kaydı yazılamadı (test)")

    monkeypatch.setattr("app.audit.record", failing_record)
    async with factory() as s:
        doc = await _load(s, doc_id)
        with pytest.raises(RuntimeError):
            await post_document(s, doc)  # satırlar flush edildikten sonra hata
        assert not s.in_transaction()
        assert await _try_write(path)
        assert await _lines(factory, entry.id) == old_lines  # kısmi yazma yok

        monkeypatch.undo()
        result = await post_document(s, doc)  # aynı oturum yeniden kullanılabilir
        assert result.status == "draft_ready" and result.entry_id == str(entry.id)
        assert not s.in_transaction()
    assert await _lines(factory, entry.id) != old_lines
    assert await _audit_count(factory, "entry.draft_regenerated") == 1


async def test_same_session_processes_same_document_twice(immediate) -> None:
    _, factory, path = immediate
    _, _, (doc_id,) = await _seed(factory)
    async with factory() as s:
        doc = await _load(s, doc_id)
        first = await post_document(s, doc)
        assert not s.in_transaction()
        second = await post_document(s, doc)
        assert not s.in_transaction()
        assert await _try_write(path)
    assert first.entry_id == second.entry_id
    assert len(await _active(factory, doc_id)) == 1


async def test_conflict_releases_lock_for_other_session(immediate) -> None:
    _, factory, _ = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    await _add_duplicate_drafts(factory, buro, muk, doc_id)
    async with factory() as s:
        doc = await _load(s, doc_id)
        assert (await post_document(s, doc)).status == "conflict"
        assert not s.in_transaction()
        async with factory() as other:  # aynı veritabanına başka oturumdan yazma
            await other.execute(update(Belge).where(Belge.id == doc_id).values(review_note="x"))
            await other.commit()
    async with factory() as s:
        assert (await s.get(Belge, doc_id)).review_note == "x"


async def test_skipped_releases_lock(immediate) -> None:
    _, factory, path = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    await _scenario("onayli", factory, buro, muk, doc_id)
    async with factory() as s:
        doc = await _load(s, doc_id)
        assert (await post_document(s, doc)).status == "skipped"
        assert not s.in_transaction()
        assert await _try_write(path)


async def test_caller_mode_keeps_lock_until_caller_ends_transaction(immediate) -> None:
    """Çağıranın transaction'ı: fonksiyon commit/rollback etmez; kilit çağıran bitirene kadar."""
    engine, factory, path = immediate
    _, _, (doc_id,) = await _seed(factory)
    async with factory() as s:
        await s.begin()
        doc = await s.get(Belge, doc_id)
        with SqlRecorder(engine) as rec:
            result = await post_document(s, doc, commit=False)
        assert result.entry_id and s.in_transaction()
        assert rec.commits == 0
        assert not any(x.upper().startswith(("COMMIT", "ROLLBACK")) for x in rec.statements)
        assert not await _try_write(path)  # kilit hâlâ çağıranın transaction'ında
        await s.commit()
        assert await _try_write(path)
    assert len(await _active(factory, doc_id)) == 1


async def test_caller_mode_commit_and_rollback_are_callers_decision(immediate) -> None:
    _, factory, _ = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    await _scenario("guncelleme", factory, buro, muk, doc_id)
    (entry,) = await _active(factory, doc_id)
    old_lines = await _lines(factory, entry.id)

    async with factory() as s:
        doc = await s.get(Belge, doc_id)
        await post_document(s, doc, commit=False)
        await s.rollback()
    assert await _lines(factory, entry.id) == old_lines
    assert await _audit_count(factory, "entry.draft_regenerated") == 0

    async with factory() as s:
        doc = await s.get(Belge, doc_id)
        await post_document(s, doc, commit=False)
        await s.commit()
    assert await _lines(factory, entry.id) != old_lines
    assert await _audit_count(factory, "entry.draft_regenerated") == 1


async def test_caller_mode_error_rolls_back_only_savepoint(immediate, monkeypatch) -> None:
    _, factory, _ = immediate
    buro, muk, (doc_id,) = await _seed(factory)
    await _scenario("guncelleme", factory, buro, muk, doc_id)
    (entry,) = await _active(factory, doc_id)
    old_lines = await _lines(factory, entry.id)

    async def failing_record(*args, **kwargs):
        raise RuntimeError("denetim kaydı yazılamadı (test)")

    async with factory() as s:
        doc = await s.get(Belge, doc_id)
        doc.review_note = "çağıranın değişikliği"
        await s.flush()
        monkeypatch.setattr("app.audit.record", failing_record)
        with pytest.raises(RuntimeError):
            await post_document(s, doc, commit=False)
        monkeypatch.undo()
        assert s.in_transaction()  # çağıranın transaction'ı kapatılmadı
        await s.commit()
    assert await _lines(factory, entry.id) == old_lines  # fonksiyonun kısmi yazması geri alındı
    async with factory() as s:
        assert (await s.get(Belge, doc_id)).review_note == "çağıranın değişikliği"
    assert await _audit_count(factory, "entry.draft_regenerated") == 0


async def test_ownership_mismatch_is_rejected_without_side_effects(immediate) -> None:
    _, factory, path = immediate
    _, _, (doc_id,) = await _seed(factory)
    before = _db_checksum(path)[0]
    async with factory() as s:
        doc = await s.get(Belge, doc_id)  # çağıranın transaction'ı açık
        with pytest.raises(IslemSahipligiHatasi):
            await post_document(s, doc)  # commit=True çağıranın transaction'ını kapatamaz
        assert s.in_transaction()
        await s.commit()
        with pytest.raises(IslemSahipligiHatasi):
            await post_document(s, doc, commit=False)  # transaction yok
        assert not s.in_transaction()
        # SQLAlchemy 2.x: kalıcı nesnede değişiklik oturumda transaction'ı otomatik başlatır;
        # bu transaction çağıranındır, fonksiyon onu reddeder ve kapatmaz.
        doc.review_note = "flush edilmemiş"
        with pytest.raises(IslemSahipligiHatasi):
            await post_document(s, doc)
        assert s.in_transaction() and doc in s.dirty
        await s.rollback()
    assert _db_checksum(path)[0] == before
