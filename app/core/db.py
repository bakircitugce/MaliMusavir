from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

# SQLite'ta eşzamanlı yazmalarda bekleme süresi (saniye). Kilit bu süre içinde açılmazsa
# "database is locked" hatası verilir.
SQLITE_BUSY_TIMEOUT_SECONDS = 30


def is_file_sqlite(database_url: str) -> bool:
    url = make_url(database_url)
    return url.drivername.startswith("sqlite") and url.database not in (None, "", ":memory:")


def engine_kwargs(database_url: str) -> dict:
    kwargs: dict = {"pool_pre_ping": True}
    if is_file_sqlite(database_url):
        kwargs["connect_args"] = {"timeout": SQLITE_BUSY_TIMEOUT_SECONDS}
    return kwargs


def configure_sqlite_transactions(engine: AsyncEngine) -> None:
    """Dosya tabanlı SQLite'ta her transaction'ı `BEGIN IMMEDIATE` ile başlatır.

    Neden: Varsayılan (DEFERRED) transaction önce okuyup sonra yazmaya geçtiğinde, başka bir
    yazar varsa SQLite beklemek yerine hemen "database is locked" döndürebilir (kilit
    yükseltme çıkmazı). IMMEDIATE, yazma kilidini transaction başında alır; diğer yazarlar
    bekleme süresi boyunca sıraya girer. Numara sayacının boşluksuz ve çakışmasız
    artmasının SQLite'taki dayanağı budur. PostgreSQL'de satır kilidi kullanılır; bu ayar
    yalnızca SQLite içindir.

    Sürücünün kendi örtük BEGIN'i kapatılır (`isolation_level = None`); böylece transaction
    başına tek bir BEGIN gönderilir, ikinci BEGIN oluşmaz.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _disable_driver_begin(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin_immediate(conn) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")


def build_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url, **engine_kwargs(database_url))
    if is_file_sqlite(database_url):
        configure_sqlite_transactions(engine)
    return engine


engine = build_engine(get_settings().database_url)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
