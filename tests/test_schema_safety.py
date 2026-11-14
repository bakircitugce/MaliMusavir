"""Part 2a: create_all kaynaklı kısmi şema riskinin kapatılması.

Kapsanan senaryolar: boş veritabanı, eski/temiz (create_all ile oluşmuş) veritabanı,
create_all yüzünden kısmen değişmiş veritabanı, geride kalmış sürüm, bilinmeyen sürüm,
yedekleme, başarısız migration, uygulama açılış kontrolü ve CLI komutları.
"""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import cli
from app import main as app_main
from app.core import schema_check
from app.core.config import get_settings
from app.core.models import Base
from app.core.schema_check import (
    EXIT_FAILED,
    EXIT_NEEDS_BACKUP,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_UNKNOWN,
    SchemaNotReadyError,
    SchemaReport,
    SchemaState,
    alembic_config,
    backup_sqlite,
    ensure_schema_ready,
    head_revision,
    inspect_database,
    run_migrate,
)

BURO_ID = "11111111111111111111111111111111"


# --- Yardımcılar ------------------------------------------------------------


def _url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def make_legacy_clean(path: Path) -> str:
    """create_all ile oluşmuş eski veritabanını taklit eder: 0001 şeması, alembic_version yok."""
    url = _url(path)
    command.upgrade(alembic_config(url), "0001")
    db = sqlite3.connect(path)
    db.execute("DROP TABLE alembic_version")
    db.execute(
        "INSERT INTO burolar (id, unvan, created_at, updated_at) "
        "VALUES (?, 'Mevcut Büro', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (BURO_ID,),
    )
    db.commit()
    db.close()
    return url


def make_partial(path: Path, *, with_rows: bool = False, drop_column: bool = False) -> str:
    """create_all'ın eski DB'de yeni tabloları erkenden oluşturduğu durum."""
    url = make_legacy_clean(path)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.tables["hesap_donemleri"].create(engine)
    Base.metadata.tables["numara_sayaclari"].create(engine)
    engine.dispose()
    db = sqlite3.connect(path)
    if with_rows:
        db.execute(
            "INSERT INTO hesap_donemleri (id, buro_id, mukellef_id, start_date, end_date, status, "
            "created_at, updated_at) VALUES ('a', ?, 'm', '2026-01-01', '2026-12-31', 'open', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (BURO_ID,),
        )
    if drop_column:
        db.execute("ALTER TABLE kullanicilar DROP COLUMN token_version")
    db.commit()
    db.close()
    return url


def schema_sql(path: Path) -> list[tuple]:
    db = sqlite3.connect(path)
    try:
        return sorted(db.execute("select type, name, sql from sqlite_master").fetchall(), key=str)
    finally:
        db.close()


def model_diffs(path: Path) -> list:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            return compare_metadata(
                MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
            )
    finally:
        engine.dispose()


def revision_of(path: Path) -> str | None:
    db = sqlite3.connect(path)
    try:
        tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        if "alembic_version" not in tables:
            return None
        return db.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        db.close()


def run(url: str, backup_dir: Path, **kwargs) -> tuple[int, str]:
    lines: list[str] = []
    code = run_migrate(url, backup_dir=backup_dir, out=lines.append, **kwargs)
    return code, "\n".join(lines)


# --- Sınıflandırma ----------------------------------------------------------


def test_classifies_every_state(tmp_path: Path) -> None:
    empty = _url(tmp_path / "bos.sqlite3")
    assert asyncio.run(inspect_database(empty)).state is SchemaState.EMPTY

    legacy = make_legacy_clean(tmp_path / "eski.sqlite3")
    assert asyncio.run(inspect_database(legacy)).state is SchemaState.LEGACY_CLEAN

    partial = asyncio.run(inspect_database(make_partial(tmp_path / "kismi.sqlite3")))
    assert partial.state is SchemaState.LEGACY_PARTIAL
    assert partial.extra_tables == {"hesap_donemleri": 0, "numara_sayaclari": 0}

    behind = tmp_path / "geride.sqlite3"
    command.upgrade(alembic_config(_url(behind)), "0001")
    report = asyncio.run(inspect_database(_url(behind)))
    assert report.state is SchemaState.BEHIND and report.current_revision == "0001"

    head = tmp_path / "guncel.sqlite3"
    command.upgrade(alembic_config(_url(head)), "head")
    assert asyncio.run(inspect_database(_url(head))).state is SchemaState.AT_HEAD

    db = sqlite3.connect(head)
    db.execute("update alembic_version set version_num='zzzz'")
    db.commit()
    db.close()
    assert asyncio.run(inspect_database(_url(head))).state is SchemaState.UNKNOWN_VERSION


def test_partial_schema_details_missing_columns(tmp_path: Path) -> None:
    report = asyncio.run(inspect_database(make_partial(tmp_path / "k.sqlite3", drop_column=True)))
    assert report.state is SchemaState.LEGACY_PARTIAL
    assert report.missing_columns == {"kullanicilar": ["token_version"]}


# --- migrate: başarılı yollar ----------------------------------------------


def test_migrate_empty_database(tmp_path: Path) -> None:
    db = tmp_path / "bos.sqlite3"
    code, out = run(_url(db), tmp_path / "yedek")
    assert code == EXIT_OK, out
    assert revision_of(db) == head_revision()
    assert model_diffs(db) == []
    assert not (tmp_path / "yedek").exists()  # boş DB için yedek gerekmez


def test_migrate_legacy_clean_backs_up_stamps_and_preserves_data(tmp_path: Path) -> None:
    db = tmp_path / "eski.sqlite3"
    url = make_legacy_clean(db)
    code, out = run(url, tmp_path / "yedek")
    assert code == EXIT_OK, out
    assert "birebir doğrulandı" in out
    assert revision_of(db) == head_revision()
    assert model_diffs(db) == []
    assert sqlite3.connect(db).execute("select unvan from burolar").fetchone() == ("Mevcut Büro",)

    backups = list((tmp_path / "yedek").glob("*.sqlite3"))
    assert len(backups) == 1
    bak = sqlite3.connect(backups[0])
    assert bak.execute("pragma integrity_check").fetchone()[0] == "ok"
    assert bak.execute("select count(*) from burolar").fetchone()[0] == 1
    assert revision_of(backups[0]) is None  # yedek, migration öncesi hali


def test_migrate_behind_database(tmp_path: Path) -> None:
    db = tmp_path / "geride.sqlite3"
    command.upgrade(alembic_config(_url(db)), "0001")
    code, out = run(_url(db), tmp_path / "yedek")
    assert code == EXIT_OK, out
    assert revision_of(db) == head_revision()
    assert len(list((tmp_path / "yedek").glob("*.sqlite3"))) == 1


def test_migrate_is_noop_at_head(tmp_path: Path) -> None:
    db = tmp_path / "guncel.sqlite3"
    command.upgrade(alembic_config(_url(db)), "head")
    before = schema_sql(db)
    code, out = run(_url(db), tmp_path / "yedek")
    assert code == EXIT_OK and "zaten güncel" in out
    assert schema_sql(db) == before
    assert not (tmp_path / "yedek").exists()


# --- migrate: korumalı yollar ----------------------------------------------


def test_migrate_never_touches_partial_schema(tmp_path: Path) -> None:
    db = tmp_path / "kismi.sqlite3"
    url = make_partial(db)
    before = schema_sql(db)
    code, out = run(url, tmp_path / "yedek")
    assert code == EXIT_PARTIAL
    assert "KISMİ ŞEMA" in out and "Hiçbir değişiklik yapılmadı" in out
    assert "hesap_donemleri" in out and "silinebilirler" in out
    assert schema_sql(db) == before  # tablo/sütun silinmedi, stamp yapılmadı
    assert revision_of(db) is None
    assert len(list((tmp_path / "yedek").glob("*.sqlite3"))) == 1


def test_partial_recovery_plan_flags_data_and_missing_columns(tmp_path: Path) -> None:
    code, out = run(make_partial(tmp_path / "k.sqlite3", with_rows=True, drop_column=True), tmp_path / "y")
    assert code == EXIT_PARTIAL
    assert "VERİ VAR" in out
    assert "Eksik sütunlar [kullanicilar]: token_version" in out


def test_migrate_refuses_unknown_version(tmp_path: Path) -> None:
    db = tmp_path / "bilinmeyen.sqlite3"
    command.upgrade(alembic_config(_url(db)), "head")
    conn = sqlite3.connect(db)
    conn.execute("update alembic_version set version_num='zzzz'")
    conn.commit()
    conn.close()
    before = schema_sql(db)
    code, _ = run(_url(db), tmp_path / "yedek")
    assert code == EXIT_UNKNOWN
    assert schema_sql(db) == before


def test_failed_migration_reports_backup_and_keeps_it_intact(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "eski.sqlite3"
    url = make_legacy_clean(db)

    def boom(config, revision):
        raise RuntimeError("yapay migration hatası")

    monkeypatch.setattr(schema_check.command, "upgrade", boom)
    code, out = run(url, tmp_path / "yedek")
    assert code == EXIT_FAILED
    assert "MIGRATION BAŞARISIZ" in out and "yapay migration hatası" in out
    backups = list((tmp_path / "yedek").glob("*.sqlite3"))
    assert len(backups) == 1 and str(backups[0]) in out
    assert revision_of(backups[0]) is None
    assert sqlite3.connect(backups[0]).execute("select count(*) from burolar").fetchone()[0] == 1


def test_non_sqlite_requires_explicit_backup(tmp_path: Path, monkeypatch) -> None:
    async def fake_inspect(url):
        return SchemaReport(SchemaState.LEGACY_CLEAN, head_revision())

    monkeypatch.setattr(schema_check, "inspect_database", fake_inspect)
    upgrades: list = []
    monkeypatch.setattr(schema_check.command, "upgrade", lambda *a: upgrades.append(a))
    monkeypatch.setattr(schema_check.command, "stamp", lambda *a: upgrades.append(a))
    code, out = run("postgresql+asyncpg://u:p@localhost/db", tmp_path / "yedek")
    assert code == EXIT_NEEDS_BACKUP and "--yedek-alindi" in out
    assert upgrades == []


# --- Yedekleme --------------------------------------------------------------


def test_backup_is_consistent_while_a_writer_is_active(tmp_path: Path) -> None:
    db = tmp_path / "canli.sqlite3"
    url = make_legacy_clean(db)
    writer = sqlite3.connect(db)
    writer.execute("BEGIN")
    writer.execute(
        "INSERT INTO burolar (id, unvan, created_at, updated_at) "
        "VALUES ('22222222222222222222222222222222', 'Yarım', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    try:
        backup = backup_sqlite(url, backup_dir=tmp_path / "yedek")
    finally:
        writer.rollback()
        writer.close()
    bak = sqlite3.connect(backup)
    assert bak.execute("pragma integrity_check").fetchone()[0] == "ok"
    assert bak.execute("select count(*) from burolar").fetchone()[0] == 1  # commit edilmemiş satır yok


def test_backup_skips_memory_and_missing_databases(tmp_path: Path) -> None:
    assert backup_sqlite("sqlite+aiosqlite:///:memory:", backup_dir=tmp_path) is None
    assert backup_sqlite(_url(tmp_path / "yok.sqlite3"), backup_dir=tmp_path) is None


# --- Açılış kontrolü ve CLI -------------------------------------------------


async def _ready(url: str) -> None:
    engine = create_async_engine(url)
    try:
        await ensure_schema_ready(engine)
    finally:
        await engine.dispose()


def test_startup_refuses_anything_but_head(tmp_path: Path) -> None:
    with pytest.raises(SchemaNotReadyError, match="boş"):
        asyncio.run(_ready(_url(tmp_path / "bos.sqlite3")))
    with pytest.raises(SchemaNotReadyError, match="eski"):
        asyncio.run(_ready(make_legacy_clean(tmp_path / "eski.sqlite3")))
    behind = tmp_path / "geride.sqlite3"
    command.upgrade(alembic_config(_url(behind)), "0001")
    with pytest.raises(SchemaNotReadyError, match="0001"):
        asyncio.run(_ready(_url(behind)))
    head = tmp_path / "guncel.sqlite3"
    command.upgrade(alembic_config(_url(head)), "head")
    asyncio.run(_ready(_url(head)))


def test_app_lifespan_does_not_create_schema(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "eski.sqlite3"
    url = make_legacy_clean(db)
    before = schema_sql(db)
    engine = create_async_engine(url)
    monkeypatch.setattr(app_main, "engine", engine)

    async def start() -> None:
        async with app_main.lifespan(app_main.app):
            pass

    with pytest.raises(SchemaNotReadyError, match="migrate"):
        asyncio.run(start())
    asyncio.run(engine.dispose())
    assert schema_sql(db) == before  # eskiden burada create_all kısmi şema üretiyordu


def test_cli_commands_refuse_unmigrated_db_and_migrate_works(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "cli.sqlite3"
    url = make_legacy_clean(db)
    before = schema_sql(db)
    engine = create_async_engine(url)
    monkeypatch.setattr(cli, "engine", engine)
    monkeypatch.setattr(cli, "session_factory", async_sessionmaker(engine, expire_on_commit=False))
    with pytest.raises(SystemExit, match="migrate"):
        asyncio.run(cli.secure_db())
    assert schema_sql(db) == before

    monkeypatch.setattr(get_settings(), "database_url", url)
    monkeypatch.setattr(schema_check, "BACKUP_DIR", tmp_path / "yedek")
    assert cli.main(["migrate"]) == EXIT_OK
    assert revision_of(db) == head_revision()
    asyncio.run(cli.secure_db())  # artık şema güncel; komut çalışır
    asyncio.run(engine.dispose())
