"""Şema sürüm kontrolü ve güvenli migration (Faz 2 / Part 2a).

Sorun: Uygulama eskiden her açılışta `create_all` çalıştırıyordu. Model değiştikten sonra
eski bir veritabanında `create_all` yeni TABLOLARI oluşturur ama mevcut tablolara SÜTUN
eklemez; ortaya kısmi bir şema çıkar. Uygulama "no such column" hatasıyla çalışmaz ve
Alembic migration'ı "table already exists" hatasıyla durur.

Çözüm:
- Uygulama açılışta şema kurmaz; yalnızca veritabanının Alembic'in son sürümünde (head)
  olduğunu doğrular, değilse açık bir mesajla durur.
- `python -m app.cli migrate` veritabanını sınıflandırır:
    EMPTY           -> sıfırdan `upgrade head`
    LEGACY_CLEAN    -> yapısı 0001 baseline ile birebir doğrulanmış eski DB:
                       `stamp 0001` + `upgrade head` (stamp SQL çalıştırmaz; yalnızca
                       doğrulama başarılıysa yapılır)
    BEHIND          -> `upgrade head`
    AT_HEAD         -> işlem yok
    LEGACY_PARTIAL  -> HİÇBİR DEĞİŞİKLİK YAPILMAZ: yedek alınır, analiz ve kurtarma planı
                       raporlanır (kurtarma ayrıca onayla, elle yapılır)
    UNKNOWN_VERSION -> işlem yapılmaz
- SQLite'ta işlem öncesi tutarlı çevrim içi yedek alınır; diğer veritabanlarında kullanıcının
  yedeği aldığını açıkça beyan etmesi istenir.
"""

import asyncio
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
BASELINE_REVISION = "0001"
VERSION_TABLE = "alembic_version"
BACKUP_DIR = ROOT / "backups"


class SchemaState(StrEnum):
    EMPTY = "empty"
    AT_HEAD = "at_head"
    BEHIND = "behind"
    LEGACY_CLEAN = "legacy_clean"
    LEGACY_PARTIAL = "legacy_partial"
    UNKNOWN_VERSION = "unknown_version"


class SchemaNotReadyError(RuntimeError):
    pass


# --- Alembic yardımcıları ---------------------------------------------------


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    if database_url:
        config.attributes["database_url"] = database_url
    return config


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def known_revisions() -> set[str]:
    return {rev.revision for rev in ScriptDirectory.from_config(alembic_config()).walk_revisions()}


# --- Yapısal anlık görüntü --------------------------------------------------

Snapshot = dict[str, dict[str, set[str]]]


def snapshot(conn: Connection) -> Snapshot:
    """Tablo, sütun ve isimli indeks/tekil kısıt adları (tipten bağımsız, lehçe bağımsız)."""
    inspector = inspect(conn)
    result: Snapshot = {}
    for table in inspector.get_table_names():
        if table == VERSION_TABLE or table.startswith("sqlite_"):
            continue
        result[table] = {
            "columns": {c["name"] for c in inspector.get_columns(table)},
            "indexes": {i["name"] for i in inspector.get_indexes(table) if i.get("name")},
            "uniques": {
                u["name"] for u in inspector.get_unique_constraints(table) if u.get("name")
            },
        }
    return result


@lru_cache(maxsize=4)
def reference_snapshot(revision: str = BASELINE_REVISION) -> Snapshot:
    """Verilen revizyonun şemasını geçici bir SQLite dosyasında kurup yapısını döner."""
    from sqlalchemy import create_engine

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reference.sqlite3"
        command.upgrade(alembic_config(f"sqlite+aiosqlite:///{path}"), revision)
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.connect() as conn:
                return snapshot(conn)
        finally:
            engine.dispose()


# --- Sınıflandırma ----------------------------------------------------------


@dataclass
class SchemaReport:
    state: SchemaState
    head: str
    current_revision: str | None = None
    extra_tables: dict[str, int] = field(default_factory=dict)  # tablo -> satır sayısı
    missing_tables: list[str] = field(default_factory=list)
    extra_columns: dict[str, list[str]] = field(default_factory=dict)
    missing_columns: dict[str, list[str]] = field(default_factory=dict)
    index_differences: dict[str, list[str]] = field(default_factory=dict)

    def describe(self) -> list[str]:
        lines = [f"Durum: {self.state.value} | mevcut sürüm: {self.current_revision or '-'} | hedef: {self.head}"]
        if self.extra_tables:
            lines.append("Baseline'da olmayan tablolar (satır sayısı): " + ", ".join(
                f"{t} ({n})" for t, n in sorted(self.extra_tables.items())))
        if self.missing_tables:
            lines.append("Eksik baseline tabloları: " + ", ".join(sorted(self.missing_tables)))
        for label, data in (("Fazla sütunlar", self.extra_columns), ("Eksik sütunlar", self.missing_columns),
                            ("İndeks/kısıt farkları", self.index_differences)):
            for table, items in sorted(data.items()):
                lines.append(f"{label} [{table}]: {', '.join(sorted(items))}")
        return lines

    def recovery_plan(self) -> list[str]:
        """Kısmi şema için önerilen kurtarma adımları (OTOMATİK UYGULANMAZ)."""
        if self.state is not SchemaState.LEGACY_PARTIAL:
            return []
        plan = ["1. Alınan yedeğin açıldığını ve bütünlüğünü doğrulayın."]
        later = reference_snapshot(head_revision())
        empty_later = [t for t, n in self.extra_tables.items() if n == 0 and t in later]
        with_rows = [t for t, n in self.extra_tables.items() if n > 0]
        unknown = [t for t in self.extra_tables if t not in later]
        step = 2
        if empty_later:
            plan.append(
                f"{step}. Şu tablolar sonraki migration'lara ait ve BOŞ; büyük olasılıkla "
                f"create_all ile erken oluşturuldu. Onaydan sonra silinebilirler: "
                + ", ".join(sorted(empty_later))
            )
            step += 1
        if with_rows:
            plan.append(
                f"{step}. Şu tablolarda VERİ VAR; silinmeden önce veri incelenmeli/taşınmalı: "
                + ", ".join(sorted(with_rows))
            )
            step += 1
        if unknown:
            plan.append(f"{step}. Migration geçmişinde olmayan tablolar elle incelenmeli: " + ", ".join(sorted(unknown)))
            step += 1
        if self.missing_tables or self.missing_columns:
            plan.append(
                f"{step}. Eksik baseline tablo/sütunları var; eski bir yedekten dönmek veya eksikleri "
                "elle eklemek gerekir (ör. eski secure-db sütunları)."
            )
            step += 1
        plan.append(f"{step}. Şema 0001 ile birebir olduktan sonra: `python -m app.cli migrate` tekrar çalıştırılır.")
        return plan


def current_revision(conn: Connection) -> str | None:
    if VERSION_TABLE not in inspect(conn).get_table_names():
        return None
    return conn.execute(text(f"SELECT version_num FROM {VERSION_TABLE}")).scalar()


def classify(conn: Connection, reference: Snapshot, head: str, known: set[str]) -> SchemaReport:
    revision = current_revision(conn)
    if revision is not None:
        if revision == head:
            return SchemaReport(SchemaState.AT_HEAD, head, revision)
        if revision in known:
            return SchemaReport(SchemaState.BEHIND, head, revision)
        return SchemaReport(SchemaState.UNKNOWN_VERSION, head, revision)

    current = snapshot(conn)
    if not current:
        return SchemaReport(SchemaState.EMPTY, head)

    report = SchemaReport(SchemaState.LEGACY_CLEAN, head)
    for table in sorted(set(current) - set(reference)):
        count = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
        report.extra_tables[table] = int(count)
    report.missing_tables = sorted(set(reference) - set(current))
    for table in set(current) & set(reference):
        extra = current[table]["columns"] - reference[table]["columns"]
        missing = reference[table]["columns"] - current[table]["columns"]
        if extra:
            report.extra_columns[table] = sorted(extra)
        if missing:
            report.missing_columns[table] = sorted(missing)
        index_diff = (current[table]["indexes"] ^ reference[table]["indexes"]) | (
            current[table]["uniques"] ^ reference[table]["uniques"]
        )
        if index_diff:
            report.index_differences[table] = sorted(index_diff)
    if any((report.extra_tables, report.missing_tables, report.extra_columns,
            report.missing_columns, report.index_differences)):
        report.state = SchemaState.LEGACY_PARTIAL
    return report


async def inspect_database(database_url: str) -> SchemaReport:
    """Hedef veritabanını salt-okunur inceler (referans şema ayrı süreçte kurulur)."""
    reference = await asyncio.to_thread(reference_snapshot, BASELINE_REVISION)
    head, known = head_revision(), known_revisions()
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(classify, reference, head, known)
    finally:
        await engine.dispose()


# --- Açılış kontrolü --------------------------------------------------------


async def ensure_schema_ready(engine: AsyncEngine) -> None:
    """Uygulama açılışında çağrılır: şema kurmaz, yalnızca head'de olduğunu doğrular."""
    async with engine.connect() as conn:
        revision = await conn.run_sync(current_revision)
        has_tables = bool(await conn.run_sync(snapshot))
    head = head_revision()
    if revision == head:
        return
    if revision is None and not has_tables:
        reason = "Veritabanı boş."
    elif revision is None:
        reason = "Veritabanı eski (Alembic öncesi) şemada."
    else:
        reason = f"Veritabanı şema sürümü {revision}, beklenen {head}."
    raise SchemaNotReadyError(
        f"{reason} Uygulama başlatılmadı. Önce `python -m app.cli migrate` çalıştırın."
    )


# --- Yedek ------------------------------------------------------------------


def sqlite_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def backup_sqlite(database_url: str, label: str = "pre-migrate", backup_dir: Path | None = None) -> Path | None:
    """SQLite için tutarlı çevrim içi yedek (çalışan bir süreç varken de güvenli)."""
    path = sqlite_path(database_url)
    if path is None or not path.exists() or path.stat().st_size == 0:
        return None
    target_dir = backup_dir or BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{path.stem}.{stamp}.{label}.sqlite3"
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    check = sqlite3.connect(target)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"Yedek bütünlük kontrolünü geçemedi: {target}")
    finally:
        check.close()
    return target


# --- migrate ----------------------------------------------------------------

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2
EXIT_NEEDS_BACKUP = 3
EXIT_UNKNOWN = 4


def run_migrate(
    database_url: str,
    *,
    assume_backup: bool = False,
    backup_dir: Path | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Veritabanını güvenle head'e taşır. Kısmi şemada değişiklik yapmaz."""
    report = asyncio.run(inspect_database(database_url))
    for line in report.describe():
        out(line)

    if report.state is SchemaState.AT_HEAD:
        out("Veritabanı zaten güncel; işlem yapılmadı.")
        return EXIT_OK
    if report.state is SchemaState.UNKNOWN_VERSION:
        out("Veritabanı bu kodun bilmediği bir şema sürümünde; işlem yapılmadı.")
        return EXIT_UNKNOWN

    backup = None
    if report.state is not SchemaState.EMPTY:
        if sqlite_path(database_url) is not None:
            backup = backup_sqlite(database_url, backup_dir=backup_dir)
            out(f"Yedek alındı: {backup}")
        elif not assume_backup:
            out("Bu veritabanı türünde otomatik yedek alınamıyor. Yedeği aldıktan sonra "
                "`--yedek-alindi` ile tekrar çalıştırın.")
            return EXIT_NEEDS_BACKUP

    if report.state is SchemaState.LEGACY_PARTIAL:
        out("KISMİ ŞEMA tespit edildi. Hiçbir değişiklik yapılmadı. Önerilen kurtarma planı:")
        for step in report.recovery_plan():
            out("  " + step)
        return EXIT_PARTIAL

    config = alembic_config(database_url)
    try:
        if report.state is SchemaState.LEGACY_CLEAN:
            # stamp SQL çalıştırmaz; yalnızca yapı 0001 ile birebir doğrulandığı için güvenli.
            command.stamp(config, BASELINE_REVISION)
            out(f"Eski şema 0001 baseline ile birebir doğrulandı; {BASELINE_REVISION} olarak işaretlendi.")
        command.upgrade(config, "head")
    except Exception as exc:  # noqa: BLE001
        out(f"MIGRATION BAŞARISIZ: {type(exc).__name__}: {exc}")
        if backup is not None:
            out(f"Veritabanı yarım kalmış olabilir. Geri dönmek için uygulamayı durdurup "
                f"yedeği geri kopyalayın: {backup}")
        return EXIT_FAILED

    final = asyncio.run(inspect_database(database_url))
    if final.state is not SchemaState.AT_HEAD:
        out(f"Migration sonrası beklenmeyen durum: {final.state.value}")
        return EXIT_FAILED
    out(f"Migration tamamlandı; şema sürümü {final.current_revision}.")
    return EXIT_OK
