"""F1 — PostgreSQL uyumluluk düzeltmeleri: JSONB varyantı ve lehçeye koşullu Alembic batch modu.

Testler gerçek bir PostgreSQL sunucusu gerektirmez: tip derlemesi ve DDL üretimi yoluyla lehçe
farkları doğrulanır. SQLite davranışının korunduğu da ayrıca kontrol edilir.

NOT: Model (ve `create_all`) PostgreSQL'de JSONB üretir. Mevcut Alembic baseline 0001 sütun
tiplerini `sa.JSON()` olarak sabit tanımladığından, migration ile kurulan taze PG hâlâ `json`
üretir; bu uyumsuzluk ayrıca raporlandı ve 0001 düzenlemesi onay bekliyor.
"""

import contextlib
import io

from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from alembic import command
from app.core.models import Base, JSONVariant
from app.core.schema_check import alembic_config, head_revision


def _json_columns() -> list[tuple[str, str]]:
    """Modelde PG'de JSONB'ye çözülen tüm sütunlar (tablo, sütun)."""
    cols = []
    for table in Base.metadata.tables.values():
        for col in table.c:
            with contextlib.suppress(Exception):
                if col.type.compile(dialect=postgresql.dialect()) == "JSONB":
                    cols.append((table.name, col.name))
    return cols


# --- Tip varyantı ---------------------------------------------------------------------------


def test_jsonvariant_resolves_per_dialect() -> None:
    assert JSONVariant.compile(dialect=postgresql.dialect()) == "JSONB"
    assert JSONVariant.compile(dialect=sqlite.dialect()) == "JSON"


def test_all_json_columns_use_jsonb_on_postgres_and_json_on_sqlite() -> None:
    cols = _json_columns()
    assert len(cols) == 6, f"6 JSON sütunu beklendi, bulundu: {cols}"
    for table, column in cols:
        col_type = Base.metadata.tables[table].c[column].type
        assert col_type.compile(dialect=sqlite.dialect()) == "JSON", f"{table}.{column}"


def test_create_all_ddl_emits_jsonb_on_postgres() -> None:
    """Model DDL (create_all yolu) PostgreSQL'de JSONB üretmeli."""
    belgeler = Base.metadata.tables["belgeler"]
    pg_ddl = str(CreateTable(belgeler).compile(dialect=postgresql.dialect()))
    assert "JSONB" in pg_ddl
    assert "extraction_metadata JSONB" in pg_ddl
    sqlite_ddl = str(CreateTable(belgeler).compile(dialect=sqlite.dialect()))
    assert "JSON" in sqlite_ddl and "JSONB" not in sqlite_ddl


# --- Alembic batch modu lehçeye koşullu -----------------------------------------------------


def test_sqlite_online_migration_still_reaches_head_with_batch() -> None:
    """SQLite'ta batch modu korunur: online migration 0002'ye (CHECK kısıtları dahil) ulaşır."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite+aiosqlite:///{Path(tmp) / 'm.sqlite3'}"
        command.upgrade(alembic_config(url), "head")
        sync = create_engine(f"sqlite:///{Path(tmp) / 'm.sqlite3'}")
        try:
            with sync.connect() as conn:
                insp = inspect(conn)
                assert "hesap_donemleri" in insp.get_table_names()
                assert "numara_sayaclari" in insp.get_table_names()
                cols = {c["name"] for c in insp.get_columns("yevmiye_fisleri")}
                assert {"fis_turu", "fis_no", "yevmiye_no"} <= cols
        finally:
            sync.dispose()
    assert head_revision() == "0003"


def test_postgres_offline_migration_uses_direct_alter_no_batch() -> None:
    """PostgreSQL'de batch KAPALI: doğrudan ALTER, geçici tablo yeniden-oluşturma YOK."""
    cfg = alembic_config("postgresql+asyncpg://u:p@localhost/db")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        command.upgrade(cfg, "head", sql=True)
    sql = buf.getvalue()
    assert "_alembic_tmp_" not in sql  # batch recreate işareti olmamalı
    assert "ALTER TABLE yevmiye_fisleri ADD COLUMN fis_turu" in sql
    assert "ADD CONSTRAINT ck_yevmiye_fisi_fis_turu" in sql


# --- 0003: JSON -> JSONB (PostgreSQL), SQLite no-op ------------------------------------------

JSONB_MIGRATION_COLUMNS = [
    ("denetim_kayitlari", "details"),
    ("mukellefler", "nace_kodlari"),
    ("tax_parameters", "metadata_json"),
    ("belgeler", "extraction_metadata"),
    ("belgeler", "classification_reasons"),
    ("ogrenilen_iliskiler", "metadata_json"),
]


def _offline_sql(url: str, from_rev: str, to_rev: str, downgrade: bool = False) -> str:
    cfg = alembic_config(url)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        if downgrade:
            command.downgrade(cfg, f"{from_rev}:{to_rev}", sql=True)
        else:
            command.upgrade(cfg, f"{from_rev}:{to_rev}", sql=True)
    return buf.getvalue()


def test_head_is_0003() -> None:
    assert head_revision() == "0003"


def test_postgres_0003_upgrade_converts_all_columns_to_jsonb() -> None:
    sql = _offline_sql("postgresql+asyncpg://u:p@localhost/db", "0002", "0003")
    for table, column in JSONB_MIGRATION_COLUMNS:
        assert f"ALTER TABLE {table} ALTER COLUMN {column} TYPE JSONB" in sql, f"{table}.{column}"
    assert sql.upper().count("TYPE JSONB") == len(JSONB_MIGRATION_COLUMNS)
    assert "_alembic_tmp_" not in sql


def test_postgres_0003_downgrade_reverts_to_json() -> None:
    sql = _offline_sql("postgresql+asyncpg://u:p@localhost/db", "0003", "0002", downgrade=True)
    for table, column in JSONB_MIGRATION_COLUMNS:
        assert f"ALTER TABLE {table} ALTER COLUMN {column} TYPE JSON" in sql, f"{table}.{column}"


def test_sqlite_0003_is_noop_both_directions() -> None:
    """SQLite'ta 0003 ne tablo ne sütun değiştirir (JSON/JSONB ayrımı yok); zincir 0003'e ulaşır."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chain.sqlite3"
        url = f"sqlite+aiosqlite:///{path}"
        command.upgrade(alembic_config(url), "0002")
        sync = create_engine(f"sqlite:///{path}")
        try:
            with sync.connect() as conn:
                before = {t: {c["name"] for c in inspect(conn).get_columns(t)}
                          for t, _ in JSONB_MIGRATION_COLUMNS}
        finally:
            sync.dispose()
        # 0002 -> 0003 (no-op) ve geri 0003 -> 0002
        command.upgrade(alembic_config(url), "0003")
        command.downgrade(alembic_config(url), "0002")
        command.upgrade(alembic_config(url), "0003")
        sync = create_engine(f"sqlite:///{path}")
        try:
            with sync.connect() as conn:
                insp = inspect(conn)
                after = {t: {c["name"] for c in insp.get_columns(t)}
                         for t, _ in JSONB_MIGRATION_COLUMNS}
                assert insp.get_columns("alembic_version")  # tablo var
        finally:
            sync.dispose()
        assert before == after  # SQLite şeması değişmedi
