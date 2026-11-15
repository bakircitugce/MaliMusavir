from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.core.models import Base

config = context.config
# Hedef veritabanı programdan (config.attributes["database_url"]) verilebilir; `migrate`
# komutu ve testler bu sayede uygulamanın varsayılan veritabanına dokunmadan çalışır.
DATABASE_URL = config.attributes.get("database_url") or get_settings().database_url
config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = Base.metadata


def _is_sqlite(bind_or_url) -> bool:
    """Batch modu yalnızca SQLite içindir. SQLite ALTER ile kısıt ekleyemez; batch tabloyu
    yeniden kurar. PostgreSQL'de batch gereksiz tablo yeniden-oluşturmaya yol açar; bu yüzden
    yalnızca SQLite'ta açılır."""
    name = bind_or_url.dialect.name if hasattr(bind_or_url, "dialect") else make_url(
        bind_or_url).get_backend_name()
    return name == "sqlite"


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(DATABASE_URL),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Batch modu yalnızca SQLite'ta açılır (ALTER ile kısıt ekleyemediği için tabloyu yeniden
    # kurar). PostgreSQL'de batch kapalıdır; doğrudan ALTER kullanılır (gereksiz yeniden-oluşturma
    # ve FK/indeks kaybı riskini önler).
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=_is_sqlite(connection),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    import asyncio

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
