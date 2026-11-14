"""Alembic migration'larının modellerle uyumunu ve geri alınabilirliğini doğrular.

Modelde değişiklik yapılıp migration yazılmazsa bu test başarısız olur.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.core.models import Base

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_db(tmp_path: Path, monkeypatch) -> tuple[Config, Path]:
    db_path = tmp_path / "migration.sqlite3"
    # env.py URL'yi ayarlardan okur; test için geçici dosyaya yönlendir.
    monkeypatch.setattr(get_settings(), "database_url", f"sqlite+aiosqlite:///{db_path}")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return config, db_path


def _diffs(db_path: Path) -> list:
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)


def test_migrations_match_models(alembic_db) -> None:
    config, db_path = alembic_db
    command.upgrade(config, "head")
    assert _diffs(db_path) == []


def test_migrations_are_reversible(alembic_db) -> None:
    config, db_path = alembic_db
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    tables = {
        row[0]
        for row in sqlite3.connect(db_path).execute(
            "select name from sqlite_master where type='table' and name != 'alembic_version'"
        )
    }
    assert tables == set()
    command.upgrade(config, "head")
    assert _diffs(db_path) == []


def test_new_integrity_constraints_are_enforced(alembic_db) -> None:
    config, db_path = alembic_db
    command.upgrade(config, "head")
    db = sqlite3.connect(db_path)
    sql = db.execute("select sql from sqlite_master where name='yevmiye_fisleri'").fetchone()[0]
    for name in (
        "uq_yevmiye_fisi_fis_no",
        "uq_yevmiye_fisi_yevmiye_no",
        "uq_yevmiye_fisi_ters_kayit",
        "ck_yevmiye_fisi_fis_turu",
        "ck_yevmiye_fisi_fis_no",
    ):
        assert name in sql, name
    counter_sql = db.execute("select sql from sqlite_master where name='numara_sayaclari'").fetchone()[0]
    assert "uq_numara_sayaci_kapsam" in counter_sql
