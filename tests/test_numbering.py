"""Part 2b: NumberingService ve SQLite BEGIN IMMEDIATE transaction davranışı.

Testler dosya tabanlı SQLite üzerinde, üretimdeki engine yapılandırmasıyla (BEGIN IMMEDIATE +
30 sn bekleme) çalışır; böylece eşzamanlılık gerçek ayrı bağlantılarla sınanır. PostgreSQL
testleri TEST_POSTGRES_URL tanımlıysa çalışır, değilse atlanır.
"""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import (
    SQLITE_BUSY_TIMEOUT_SECONDS,
    build_engine,
    configure_sqlite_transactions,
    engine_kwargs,
    is_file_sqlite,
)
from app.core.models import Base, Buro, HesapDonemi, JournalEntry, Mukellef, NumaraSayaci
from app.ledger.numbering import NumaralandirmaHatasi, SayacTuru, allocate

BURO = uuid4()
OTHER_BURO = uuid4()
M1, M2 = uuid4(), uuid4()


# --- Ortak kurulum ----------------------------------------------------------


async def _seed(factory: async_sessionmaker[AsyncSession]) -> dict[str, UUID]:
    ids = {"hd1": uuid4(), "hd2": uuid4(), "hd1_closed": uuid4(), "hd1_fresh": uuid4()}
    async with factory() as s:
        s.add_all([Buro(id=BURO, unvan="Büro"), Buro(id=OTHER_BURO, unvan="Diğer")])
        for m, vkn in ((M1, "1111111111"), (M2, "2222222222")):
            s.add(Mukellef(id=m, buro_id=BURO, vkn_tckn=vkn, unvan=str(m)[:6], mukellef_turu="kurumlar"))
        await s.flush()
        s.add_all([
            HesapDonemi(id=ids["hd1"], buro_id=BURO, mukellef_id=M1, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)),
            HesapDonemi(id=ids["hd2"], buro_id=BURO, mukellef_id=M2, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)),
            HesapDonemi(id=ids["hd1_closed"], buro_id=BURO, mukellef_id=M1, start_date=date(2025, 1, 1),
                        end_date=date(2025, 12, 31), status="closed"),
            HesapDonemi(id=ids["hd1_fresh"], buro_id=BURO, mukellef_id=M1, start_date=date(2027, 1, 1), end_date=date(2027, 12, 31)),
        ])
        await s.commit()
    return ids


@pytest.fixture
async def env(tmp_path) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession], dict]]:
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'num.sqlite3'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(factory)
    yield engine, factory, ids
    await engine.dispose()


async def _alloc(factory, hd, kind=SayacTuru.FIS, mukellef=M1, count=1, *, commit=True) -> range:
    async with factory() as s:
        numbers = await allocate(s, buro_id=BURO, mukellef_id=mukellef, hesap_donemi_id=hd,
                                 kind=kind, count=count)
        if commit:
            await s.commit()
        else:
            await s.rollback()
        return numbers


async def _counter(factory, hd, kind="fis", mukellef=M1) -> int | None:
    async with factory() as s:
        return await s.scalar(select(NumaraSayaci.son_numara).where(
            NumaraSayaci.mukellef_id == mukellef, NumaraSayaci.hesap_donemi_id == hd,
            NumaraSayaci.sayac_turu == kind))


# --- Engine / transaction yapılandırması -----------------------------------


def test_engine_configuration_only_for_file_sqlite() -> None:
    assert is_file_sqlite("sqlite+aiosqlite:///./x.sqlite3")
    assert not is_file_sqlite("sqlite+aiosqlite:///:memory:")
    assert not is_file_sqlite("postgresql+asyncpg://u:p@h/db")
    assert engine_kwargs("sqlite+aiosqlite:///./x.sqlite3")["connect_args"] == {
        "timeout": SQLITE_BUSY_TIMEOUT_SECONDS
    }
    assert SQLITE_BUSY_TIMEOUT_SECONDS == 30
    assert "connect_args" not in engine_kwargs("postgresql+asyncpg://u:p@h/db")


async def test_exactly_one_begin_immediate_per_transaction(env) -> None:
    engine, factory, ids = env
    statements: list[str] = []
    commits: list[int] = []

    @event.listens_for(engine.sync_engine, "commit")
    def count_commit(conn) -> None:  # COMMIT sürücünün commit() metoduyla gönderilir
        commits.append(1)

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def capture(conn, cursor, statement, *args) -> None:
        statements.append(statement.strip().split()[0].upper() + (
            " " + statement.strip().split()[1].upper() if statement.strip().upper().startswith("BEGIN") else ""))

    # 1) Otomatik transaction: okuma + yazma + savepoint + commit
    async with factory() as s:
        await s.scalar(select(func.count(Mukellef.id)))
        await allocate(s, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind=SayacTuru.FIS)
        await s.commit()
    # 2) Açık transaction bloğu
    async with factory() as s, s.begin():
        await allocate(s, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind=SayacTuru.FIS)

    begins = [st for st in statements if st.startswith("BEGIN")]
    assert begins == ["BEGIN IMMEDIATE", "BEGIN IMMEDIATE"], statements
    assert len(commits) == 2
    # İlk sayaç oluşturma SAVEPOINT içinde (iç içe BEGIN değil)
    assert "SAVEPOINT" in statements and "RELEASE" in statements


async def test_driver_does_not_emit_hidden_begin(env) -> None:
    """Sürücü kendi BEGIN'ini de gönderseydi SQLite 'cannot start a transaction within a
    transaction' hatası verirdi; yazma + savepoint + commit sorunsuz tamamlanmalı."""
    _, factory, ids = env
    async with factory() as s:
        raw = await s.connection()
        assert (await raw.exec_driver_sql("SELECT 1")).scalar() == 1
        dbapi = (await raw.get_raw_connection()).driver_connection
        assert dbapi.isolation_level is None  # örtük BEGIN kapalı
        await allocate(s, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind=SayacTuru.FIS)
        await s.commit()
    assert await _counter(factory, ids["hd1"]) == 1


async def test_second_writer_waits_instead_of_failing(env, tmp_path) -> None:
    """BEGIN IMMEDIATE: ikinci yazar hata almak yerine kilidin açılmasını bekler."""
    _, factory, ids = env
    holder = factory()
    await holder.execute(text("SELECT 1"))  # BEGIN IMMEDIATE -> yazma kilidi alındı
    started = time.perf_counter()

    async def release_later() -> None:
        await asyncio.sleep(0.5)
        await holder.commit()
        await holder.close()

    releaser = asyncio.create_task(release_later())
    numbers = await _alloc(factory, ids["hd1"])  # kilit açılana kadar bekler
    await releaser
    assert list(numbers) == [1]
    assert time.perf_counter() - started >= 0.4


async def test_busy_timeout_eventually_errors_without_corruption(env, tmp_path) -> None:
    """Kilit bekleme süresinden uzun tutulursa açık hata verilir, veri bozulmaz."""
    engine, factory, ids = env
    short = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'num.sqlite3'}",
                                connect_args={"timeout": 0.3})
    configure_sqlite_transactions(short)
    holder = factory()
    await holder.execute(text("SELECT 1"))
    try:
        with pytest.raises(OperationalError, match="locked"):
            async with async_sessionmaker(short)() as s:
                await allocate(s, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind=SayacTuru.FIS)
    finally:
        await holder.rollback()
        await holder.close()
        await short.dispose()
    assert await _counter(factory, ids["hd1"]) is None  # hiçbir şey yazılmadı


# --- Numaralandırma kuralları ----------------------------------------------


async def test_sequential_and_independent_series(env) -> None:
    _, factory, ids = env
    assert [n for _ in range(3) for n in await _alloc(factory, ids["hd1"])] == [1, 2, 3]
    # Yevmiye ayrı sayaç
    assert list(await _alloc(factory, ids["hd1"], SayacTuru.YEVMIYE)) == [1]
    # Başka mükellef ve başka hesap dönemi kendi serisinde
    assert list(await _alloc(factory, ids["hd2"], mukellef=M2)) == [1]
    assert list(await _alloc(factory, ids["hd1_fresh"])) == [1]
    # Blok ayırma
    assert list(await _alloc(factory, ids["hd1"], count=5)) == [4, 5, 6, 7, 8]
    assert await _counter(factory, ids["hd1"]) == 8
    assert await _counter(factory, ids["hd1"], "yevmiye") == 1


async def test_validation_and_tenant_scope(env) -> None:
    _, factory, ids = env
    async with factory() as s:
        with pytest.raises(NumaralandirmaHatasi, match="bulunamadı"):
            await allocate(s, buro_id=OTHER_BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind="fis")
        with pytest.raises(NumaralandirmaHatasi, match="bulunamadı"):
            await allocate(s, buro_id=BURO, mukellef_id=M2, hesap_donemi_id=ids["hd1"], kind="fis")
        with pytest.raises(NumaralandirmaHatasi, match="Kapalı"):
            await allocate(s, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1_closed"], kind="fis")
        for bad in (0, -1, 10_001):
            with pytest.raises(NumaralandirmaHatasi, match="arasında"):
                await allocate(s, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind="fis", count=bad)
        with pytest.raises(ValueError):
            await allocate(s, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind="fis_mahsup")
    assert await _counter(factory, ids["hd1"]) is None


async def test_database_constraint_is_last_line_of_defense(env) -> None:
    _, factory, ids = env
    async with factory() as s:
        s.add(NumaraSayaci(buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], sayac_turu="fis"))
        s.add(NumaraSayaci(buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], sayac_turu="fis"))
        with pytest.raises(IntegrityError):
            await s.commit()


# --- Rollback ---------------------------------------------------------------


async def test_rollback_returns_number_and_keeps_entry_consistent(env) -> None:
    _, factory, ids = env
    assert list(await _alloc(factory, ids["hd1"])) == [1]
    entry_id = uuid4()
    async with factory() as s:
        s.add(JournalEntry(id=entry_id, buro_id=BURO, mukellef_id=M1, entry_date=date(2026, 9, 1),
                           description="Rollback testi", status="draft"))
        await s.commit()

    # Numara alınıp fişe yazılıyor, sonra transaction geri alınıyor
    async with factory() as s:
        [number] = await allocate(s, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind="fis")
        entry = await s.get(JournalEntry, entry_id)
        entry.fis_no, entry.hesap_donemi_id = number, ids["hd1"]
        await s.flush()
        assert number == 2
        await s.rollback()

    async with factory() as s:
        entry = await s.get(JournalEntry, entry_id)
        assert entry.fis_no is None and entry.hesap_donemi_id is None
    assert await _counter(factory, ids["hd1"]) == 1
    # Geri alınan numara kaybolmadı: bir sonraki işlem aynı numarayı alır
    assert list(await _alloc(factory, ids["hd1"])) == [2]


async def test_rollback_of_first_counter_creation(env) -> None:
    _, factory, ids = env
    assert list(await _alloc(factory, ids["hd1_fresh"], commit=False)) == [1]
    assert await _counter(factory, ids["hd1_fresh"]) is None  # sayaç satırı da geri alındı
    assert list(await _alloc(factory, ids["hd1_fresh"])) == [1]


# --- Eşzamanlılık -----------------------------------------------------------


async def test_100_concurrent_allocations_no_duplicates_no_gaps(env) -> None:
    _, factory, ids = env
    committed: list[int] = []

    async def worker(i: int) -> None:
        async with factory() as s:
            [number] = await allocate(s, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind="fis")
            await asyncio.sleep(0)  # diğer görevlere fırsat ver
            if i % 5 == 0:  # her 5 işlemden biri numaradan sonra başarısız olur
                await s.rollback()
                return
            await s.commit()
            committed.append(number)

    await asyncio.gather(*(worker(i) for i in range(100)))
    assert len(committed) == 80
    assert sorted(committed) == list(range(1, 81))  # çift yok, boşluk yok
    assert await _counter(factory, ids["hd1"]) == 80


async def test_first_counter_creation_race(env) -> None:
    _, factory, ids = env
    results = await asyncio.gather(*(_alloc(factory, ids["hd1_fresh"]) for _ in range(50)))
    assert sorted(n for r in results for n in r) == list(range(1, 51))
    async with factory() as s:
        rows = await s.scalar(select(func.count(NumaraSayaci.id)).where(
            NumaraSayaci.hesap_donemi_id == ids["hd1_fresh"]))
    assert rows == 1


async def test_concurrent_mukellefs_keep_independent_series(env) -> None:
    _, factory, ids = env
    jobs = [_alloc(factory, ids["hd1"], mukellef=M1) for _ in range(30)] + [
        _alloc(factory, ids["hd2"], mukellef=M2) for _ in range(30)
    ]
    results = await asyncio.gather(*jobs)
    m1 = sorted(n for r in results[:30] for n in r)
    m2 = sorted(n for r in results[30:] for n in r)
    assert m1 == list(range(1, 31)) and m2 == list(range(1, 31))


async def test_sqlite_serializes_writers_across_mukellefs(env) -> None:
    """SQLite'ın bilinen sınırı: kilit veritabanı düzeyindedir; farklı mükellefler de sıraya
    girer (doğru sonuç, ama paralellik yok). PostgreSQL'deki satır kilidi testi aşağıda."""
    _, factory, ids = env
    holder = factory()
    await allocate(holder, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind="fis")
    other = asyncio.create_task(_alloc(factory, ids["hd2"], mukellef=M2))
    await asyncio.sleep(0.3)
    assert not other.done()  # M1 transaction'ı bitene kadar bekliyor
    await holder.commit()
    await holder.close()
    assert list(await asyncio.wait_for(other, 10)) == [1]


# --- PostgreSQL (TEST_POSTGRES_URL tanımlıysa) ------------------------------

PG_URL = os.environ.get("TEST_POSTGRES_URL")
pg = pytest.mark.skipif(not PG_URL, reason="TEST_POSTGRES_URL tanımlı değil (PostgreSQL yok)")


@pytest.fixture
async def pg_env() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], dict]]:
    engine = build_engine(PG_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids = await _seed(factory)
    yield factory, ids
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pg
async def test_pg_100_concurrent_allocations(pg_env) -> None:
    factory, ids = pg_env
    results = await asyncio.gather(*(_alloc(factory, ids["hd1"]) for _ in range(100)))
    assert sorted(n for r in results for n in r) == list(range(1, 101))


@pg
async def test_pg_row_lock_does_not_block_other_mukellef(pg_env) -> None:
    factory, ids = pg_env
    holder = factory()
    await allocate(holder, buro_id=BURO, mukellef_id=M1, hesap_donemi_id=ids["hd1"], kind="fis")
    # Farklı mükellef beklemeden ilerler
    assert list(await asyncio.wait_for(_alloc(factory, ids["hd2"], mukellef=M2), 2)) == [1]
    # Aynı mükellef sıraya girer
    same = asyncio.create_task(_alloc(factory, ids["hd1"]))
    await asyncio.sleep(0.3)
    assert not same.done()
    await holder.commit()
    await holder.close()
    assert list(await asyncio.wait_for(same, 5)) == [2]
