"""Okuma sonrası işlem (process_after_extraction): banka ekstresi denemesi ve fiş üretimi.

Regresyon: ekstre olmayan PDF/tablo dosyalarında ekstre denemesi transaction'ı geri alıyor,
ardından expired belge nesnesi yüzünden fiş üretimi ve hata loglaması çöküyordu.

Senaryo testleri `db_factory` kullanır (bellek içi veya TEST_DB_MODE=file). Kilit bırakma ve
commit sayısı testleri kendi dosya SQLite (IMMEDIATE) motorunu, DEFERRED testi kendi olaysız
motorunu kurar. PostgreSQL bu ortamda yoktur.
"""

import hashlib
import io
import logging
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

import pypdf
import pytest
from sqlalchemy import event, func, inspect, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bank import statement_import
from app.core.db import build_engine, configure_sqlite_transactions
from app.core.models import BankAccount, BankTransaction, Base, Belge, Buro, JournalEntry, Mukellef
from app.documents import queue
from app.documents.extractor import _ubl_metadata
from app.documents.storage import LocalStorage
from app.documents.ubl import parse_ubl_invoice
from app.ledger.draft import LedgerValidationError
from app.ledger.posting import IslemSahipligiHatasi
from tests.test_posting import ubl
from tests.test_statement_import import IBAN, _xlsx, vakif_like, ziraat_like

MUKELLEF_VKN = "1111111111"
LOGGER = "app.documents.queue"

Z_META = {"z_report": {"total": "120.00", "cash_total": "120.00", "card_total": "0",
                       "vat_breakdown": [{"rate": "20", "tax": "20.00"}],
                       "report_date": "2026-09-15", "report_number": "7"}}


def blank_pdf() -> bytes:
    """Geçerli ama metinsiz PDF (banka ekstresi değil)."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def ubl_meta() -> dict:
    content = ubl(invoice_id="SAT1", supplier_vkn=MUKELLEF_VKN, customer_vkn="3333333333")
    return {"invoice": _ubl_metadata(parse_ubl_invoice(content))}


# senaryo adı -> (belge türü, dosya adı, içerik, okuma durumu, metadata)
SCENARIOS = {
    "pdf_z_raporu": ("pdf", "z.pdf", blank_pdf, "completed", Z_META),
    "pdf_kural_yok": ("pdf", "diger.pdf", blank_pdf, "completed", {}),
    "pdf_gecersiz": ("pdf", "bozuk.pdf", lambda: b"%PDF-1.4 bozuk dosya", "failed", {}),
    "xlsx_ekstre_degil": ("spreadsheet", "liste.xlsx",
                          lambda: _xlsx([["Ürün", "Adet"], ["Kalem", 3]]), "completed", {}),
    "csv_ekstre_degil": ("spreadsheet", "liste.csv", lambda: b"urun;adet\nkalem;3\n",
                         "completed", {}),
    "gercek_ekstre": ("spreadsheet", "VAKIFBANK.xlsx", vakif_like, "completed", {}),
    "ibansiz_ekstre": ("spreadsheet", "ZIRAAT.xlsx", ziraat_like, "completed", {}),
    "dosya_yok": ("pdf", "kayip.pdf", None, "completed", Z_META),
    "ubl_xml": ("ubl_xml", "fatura.xml", lambda: b"<Invoice/>", "completed", None),
    "fotograf_z": ("image", "z.jpg", lambda: b"\xff\xd8\xff", "completed", Z_META),
}

# senaryo -> (posting durumu, fiş sayısı, banka hareketi sayısı, kategori)
EXPECTED = {
    "pdf_z_raporu": ("draft_ready", 1, 0, None),
    "pdf_kural_yok": ("needs_review", 0, 0, None),
    "pdf_gecersiz": ("needs_review", 0, 0, None),
    "xlsx_ekstre_degil": ("needs_review", 0, 0, None),
    "csv_ekstre_degil": ("needs_review", 0, 0, None),
    "gercek_ekstre": ("bank_imported", 0, 3, "bank_statement"),
    "ibansiz_ekstre": ("needs_review", 0, 0, "bank_statement"),
    "dosya_yok": ("draft_ready", 1, 0, None),
    "ubl_xml": ("draft_ready", 1, 0, None),
    "fotograf_z": ("draft_ready", 1, 0, None),
}


async def seed(factory, root: Path, name: str) -> UUID:
    doc_type, filename, content, status, meta = SCENARIOS[name]
    buro, muk, doc_id = uuid4(), uuid4(), uuid4()
    key = f"{doc_id}/{filename}"
    if content is not None:
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content())
    async with factory() as s:
        s.add(Buro(id=buro, unvan="B"))
        s.add(Mukellef(id=muk, buro_id=buro, vkn_tckn=MUKELLEF_VKN, unvan="M",
                       mukellef_turu="kurumlar"))
        s.add(Belge(id=doc_id, buro_id=buro, mukellef_id=muk, original_filename=filename,
                    content_type="application/octet-stream", size_bytes=1,
                    sha256=hashlib.sha256(doc_id.bytes).hexdigest(), storage_key=key,
                    document_type=doc_type, extraction_status=status,
                    extraction_metadata=ubl_meta() if meta is None else meta))
        await s.commit()
    return doc_id


async def run(factory, root: Path, doc_id: UUID):
    """Uygulamadaki gibi: okuma adımı belgeyi yeniden okumuş (açık okuma transaction'ı) halde."""
    async with factory() as s:
        doc = await s.get(Belge, doc_id)
        assert s.in_transaction()
        await queue.process_after_extraction(s, doc, LocalStorage(root=root))
        assert not s.in_transaction()  # transaction her yolda kapatıldı
    return doc


async def state(factory, doc_id: UUID) -> tuple:
    async with factory() as s:
        doc = await s.get(Belge, doc_id)
        entries = await s.scalar(select(func.count()).select_from(JournalEntry)
                                 .where(JournalEntry.source_document_id == doc_id))
        txs = await s.scalar(select(func.count()).select_from(BankTransaction)
                             .where(BankTransaction.mukellef_id == doc.mukellef_id))
    posting = (doc.extraction_metadata or {}).get("posting") or {}
    return posting.get("status"), entries, txs, doc.document_category, doc


def errors(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.levelno >= logging.ERROR]


# --- senaryolar (bellek içi / dosya modu) -------------------------------------------------------


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_processing_scenarios(db_factory, tmp_path, caplog, name) -> None:
    doc_id = await seed(db_factory, tmp_path, name)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        await run(db_factory, tmp_path, doc_id)
    assert errors(caplog) == []
    status, entries, txs, category, doc = await state(db_factory, doc_id)
    assert (status, entries, txs, category) == EXPECTED[name]
    if name == "gercek_ekstre":
        assert doc.review_status == "approved"
    if name == "pdf_gecersiz":
        assert "okunamadı" in " ".join(doc.extraction_metadata["posting"]["reasons"])


async def test_ubl_and_photo_never_attempt_bank_import(db_factory, tmp_path, monkeypatch) -> None:
    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("UBL/fotoğraf için ekstre denemesi yapılmamalı")

    monkeypatch.setattr(queue, "import_statement", must_not_be_called)
    for name in ("ubl_xml", "fotograf_z"):
        doc_id = await seed(db_factory, tmp_path, name)
        await run(db_factory, tmp_path, doc_id)
        assert (await state(db_factory, doc_id))[:2] == ("draft_ready", 1)


async def test_partial_bank_import_failure_rolls_back_only_savepoint(
    db_factory, tmp_path, monkeypatch
) -> None:
    async def fail_after_creating_account(session, *, buro_id, mukellef_id, **kwargs):
        await statement_import._ensure_account(session, buro_id, mukellef_id, IBAN, "Banka")
        await session.flush()
        raise LedgerValidationError("hareket doğrulaması başarısız (test)")

    monkeypatch.setattr(queue, "import_statement", fail_after_creating_account)
    doc_id = await seed(db_factory, tmp_path, "pdf_z_raporu")
    async with db_factory() as s:
        doc = await s.get(Belge, doc_id)
        assert await queue._try_bank_statement(s, doc, LocalStorage(root=tmp_path)) is False
        assert s.in_transaction()  # dış transaction açık; yardımcı commit/rollback yapmadı
        assert not inspect(doc).expired_attributes  # belge açıkça yeniden okundu
        assert await s.scalar(select(func.count()).select_from(BankAccount)) == 0
        await s.rollback()

    await run(db_factory, tmp_path, doc_id)  # tam akış: fiş yoluna devam eder
    status, entries, _, _, _ = await state(db_factory, doc_id)
    assert (status, entries) == ("draft_ready", 1)
    async with db_factory() as s:
        assert await s.scalar(select(func.count()).select_from(BankAccount)) == 0


async def test_autoflush_before_savepoint_keeps_pending_changes(
    db_factory, tmp_path, monkeypatch
) -> None:
    """begin_nested() bekleyen değişikliği SAVEPOINT'ten önce dış transaction'a flush eder."""
    async def fail(session, *, buro_id, mukellef_id, **kwargs):
        await statement_import._ensure_account(session, buro_id, mukellef_id, IBAN, "Banka")
        await session.flush()
        raise LedgerValidationError("test")

    monkeypatch.setattr(queue, "import_statement", fail)
    doc_id = await seed(db_factory, tmp_path, "pdf_kural_yok")
    async with db_factory() as s:
        doc = await s.get(Belge, doc_id)
        doc.review_note = "SAVEPOINT öncesi"  # flush edilmemiş değişiklik
        assert await queue._try_bank_statement(s, doc, LocalStorage(root=tmp_path)) is False
        # Flush sonrası sunucu tarafı alanlar (updated_at) expired kalırdı; açık refresh ile
        # hiçbir alan gizli IO gerektirmez.
        assert not inspect(doc).expired_attributes
        assert doc.review_note == "SAVEPOINT öncesi"  # dış transaction'da korundu
        assert await s.scalar(select(func.count()).select_from(BankAccount)) == 0
        await s.commit()
    async with db_factory() as s:
        assert (await s.get(Belge, doc_id)).review_note == "SAVEPOINT öncesi"


@pytest.fixture
async def immediate_factory(tmp_path):
    """Gerçek transaction semantiği: dosya SQLite + BEGIN IMMEDIATE (üretim yapılandırması).

    Varsayılan sürücü modunda (bellek içi test motoru, gerçek DEFERRED) SELECT sonrası açılan
    SAVEPOINT en dış transaction'ı başlatır ve RELEASE SAVEPOINT fiilen commit eder; çağıranın
    sonraki rollback'i onu geri alamaz. Bu sürücü sınırlaması nedeniyle SAVEPOINT'in çağıranın
    transaction'ı içinde kaldığını doğrulayan test bu motorla çalışır.
    """
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'immediate.sqlite3'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.parametrize("name", ["gercek_ekstre", "ibansiz_ekstre", "pdf_kural_yok",
                                  "pdf_gecersiz", "dosya_yok"])
async def test_bank_helper_never_ends_callers_transaction(immediate_factory, tmp_path, name) -> None:
    db_factory = immediate_factory
    doc_id = await seed(db_factory, tmp_path, name)
    engine = db_factory.kw["bind"].sync_engine
    ends: list[str] = []
    on_commit = lambda conn: ends.append("COMMIT")
    on_rollback = lambda conn: ends.append("ROLLBACK")
    event.listen(engine, "commit", on_commit)
    event.listen(engine, "rollback", on_rollback)
    try:
        async with db_factory() as s:
            doc = await s.get(Belge, doc_id)
            await queue._try_bank_statement(s, doc, LocalStorage(root=tmp_path))
            assert s.in_transaction() and ends == []  # yardımcı transaction'ı kapatmadı
            await s.rollback()  # çağıranın kararı: geri al
    finally:
        event.remove(engine, "commit", on_commit)
        event.remove(engine, "rollback", on_rollback)
    status, entries, txs, category, _ = await state(db_factory, doc_id)
    assert (status, entries, txs, category) == (None, 0, 0, None)  # hiçbir şey kalıcı olmadı


async def test_pending_changes_are_rejected_without_side_effects(db_factory, tmp_path) -> None:
    doc_id = await seed(db_factory, tmp_path, "pdf_z_raporu")
    async with db_factory() as s:
        doc = await s.get(Belge, doc_id)
        doc.review_note = "çağıranın değişikliği"
        with pytest.raises(IslemSahipligiHatasi):
            await queue.process_after_extraction(s, doc, LocalStorage(root=tmp_path))
        assert doc in s.dirty and s.in_transaction()  # çağıranın durumu olduğu gibi
        await s.rollback()
    status, entries, *_ = await state(db_factory, doc_id)
    assert (status, entries) == (None, 0)


async def test_unexpected_error_is_logged_once_with_saved_id(
    db_factory, tmp_path, monkeypatch, caplog
) -> None:
    """Ekstre denemesi başarısız + fiş üretiminde beklenmeyen hata: tek log, ikincil hata yok."""
    async def boom(*args, **kwargs):
        raise RuntimeError("beklenmeyen hata (test)")

    monkeypatch.setattr(queue, "post_document", boom)
    doc_id = await seed(db_factory, tmp_path, "pdf_z_raporu")
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        await run(db_factory, tmp_path, doc_id)
    records = errors(caplog)
    assert len(records) == 1
    assert str(doc_id) in records[0].getMessage()
    assert records[0].exc_info[0] is RuntimeError
    status, entries, *_ = await state(db_factory, doc_id)
    assert (status, entries) == (None, 0)  # rollback: kısmi yazma yok


async def test_error_after_document_modified_inside_posting_savepoint(
    db_factory, tmp_path, monkeypatch, caplog
) -> None:
    """post_document belgeyi SAVEPOINT içinde değiştirip hata verirse SAVEPOINT geri alınır ve
    belge "expired" olur; log yine de saklanan kimlikle, ikincil hata olmadan yazılmalı."""
    from app.ledger import posting

    original_save = posting._save

    async def save_then_fail(session, document, result):
        await original_save(session, document, result)  # belge değişti ve flush edildi
        raise RuntimeError("sonuç yazıldıktan sonra hata (test)")

    monkeypatch.setattr(posting, "_save", save_then_fail)
    doc_id = await seed(db_factory, tmp_path, "pdf_kural_yok")
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        await run(db_factory, tmp_path, doc_id)
    records = errors(caplog)
    assert len(records) == 1 and str(doc_id) in records[0].getMessage()
    assert records[0].exc_info[0] is RuntimeError
    status, entries, *_ = await state(db_factory, doc_id)
    assert (status, entries) == (None, 0)


async def test_rollback_failure_is_logged_and_not_raised(
    db_factory, tmp_path, monkeypatch, caplog
) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("beklenmeyen hata (test)")

    monkeypatch.setattr(queue, "post_document", boom)
    doc_id = await seed(db_factory, tmp_path, "pdf_kural_yok")
    async with db_factory() as s:
        doc = await s.get(Belge, doc_id)

        async def failing_rollback():
            raise OperationalError("ROLLBACK", {}, Exception("test"))

        monkeypatch.setattr(s, "rollback", failing_rollback)
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            await queue.process_after_extraction(s, doc, LocalStorage(root=tmp_path))
        monkeypatch.undo()
    messages = [r.getMessage() for r in errors(caplog)]
    assert len(messages) == 2 and all(str(doc_id) in m for m in messages)


async def test_background_task_end_to_end(db_factory, tmp_path, monkeypatch, caplog) -> None:
    """_run_extraction: gerçek okuma + ekstre denemesi + fiş yolu; görevden hata kaçmaz."""
    monkeypatch.setattr(queue, "session_factory", db_factory)
    monkeypatch.setattr(queue, "LocalStorage", lambda: LocalStorage(root=tmp_path))
    doc_id = await seed(db_factory, tmp_path, "pdf_kural_yok")
    async with db_factory() as s:
        doc = await s.get(Belge, doc_id)
        buro_id, mukellef_id = doc.buro_id, doc.mukellef_id
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        await queue._run_extraction(doc_id, buro_id, mukellef_id)
    assert errors(caplog) == []
    status, entries, *_ = await state(db_factory, doc_id)
    assert status == "needs_review" and entries == 0


async def test_extract_endpoint_with_non_statement_pdf(api_client, db_factory, tmp_path,
                                                       monkeypatch) -> None:
    from app.documents import router as documents_router
    from tests.auth_helpers import make_actor, make_buro

    monkeypatch.setattr(documents_router, "storage", LocalStorage(root=tmp_path))
    monkeypatch.setattr(documents_router, "enqueue_extraction", lambda *a, **k: None)
    m = uuid4()
    buro = await make_buro(db_factory, mukellef_ids=[m])
    actor = await make_actor(db_factory, buro, rol="personel", assigned=[m])
    up = await api_client.post("/documents/upload", headers=actor.headers(m),
                               files={"upload": ("belge.pdf", blank_pdf(), "application/pdf")})
    assert up.status_code == 201, up.text
    doc_id = up.json()["id"]
    res = await api_client.post(f"/documents/{doc_id}/extract", headers=actor.headers(m))
    assert res.status_code in (200, 422), res.text  # 500 değil
    detail = (await api_client.get(f"/documents/{doc_id}", headers=actor.headers(m))).json()
    assert detail["extraction_metadata"]["posting"]["status"] == "needs_review"


# --- dosya SQLite IMMEDIATE: tek commit ve kilidin bırakılması -----------------------------------


async def _try_write(path: Path) -> bool:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 0.3})
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


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_file_immediate_single_commit_and_lock_released(tmp_path, name) -> None:
    path = tmp_path / "q.sqlite3"
    engine = build_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    doc_id = await seed(factory, tmp_path, name)
    commits: list[int] = []
    event.listen(engine.sync_engine, "commit", lambda conn: commits.append(1))
    async with factory() as s:
        doc = await s.get(Belge, doc_id)
        await queue.process_after_extraction(s, doc, LocalStorage(root=tmp_path))
        assert not s.in_transaction()
        assert await _try_write(path)  # oturum açıkken kilit bırakılmış
    # get() ile açılan okuma transaction'ı devralınır; yol başına tek COMMIT
    assert len(commits) == 1
    assert (await state(factory, doc_id))[:4] == EXPECTED[name]
    await engine.dispose()


# --- gerçek DEFERRED motor -----------------------------------------------------------------------


def _checksum(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return hashlib.sha256("\n".join(conn.iterdump()).encode()).hexdigest()
    finally:
        conn.close()


@pytest.mark.parametrize("name", ["pdf_z_raporu", "ubl_xml"])
async def test_real_deferred_engine_fails_closed_without_crash(tmp_path, caplog, name) -> None:
    path = tmp_path / "deferred.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")  # olaysız: gerçek DEFERRED
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    doc_id = await seed(factory, tmp_path, name)
    await engine.dispose()
    before = _checksum(path)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        await run(factory, tmp_path, doc_id)
    await engine.dispose()

    records = errors(caplog)
    assert len(records) == 1 and str(doc_id) in records[0].getMessage()
    assert records[0].exc_info[0].__name__ == "KilitGarantisiYok"
    assert _checksum(path) == before  # hiçbir yazma kalmadı
