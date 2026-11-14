import os

# Ayarlar app import edilmeden önce belirlenmeli (get_settings import anında okunur).
os.environ.setdefault("AUTH_SECRET", "test-only-secret-" + "x" * 48)
os.environ["APP_ENV"] = "test"
# Testlerde hız için düşük iterasyon; config yalnızca APP_ENV=test iken buna izin verir.
os.environ.setdefault("PASSWORD_HASH_ITERATIONS", "1000")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.router import login_ip_limiter, login_limiter  # noqa: E402
from app.core.db import build_engine, get_session  # noqa: E402
from app.core.models import Base  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_login_limiter() -> None:
    login_limiter.reset()
    login_ip_limiter.reset()


@pytest.fixture
async def db_factory(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # TEST_DB_MODE=file: üretimdeki gibi dosya tabanlı SQLite + BEGIN IMMEDIATE + bekleme
    # süresi ile çalıştırır (transaction davranışı değişikliğinin tüm akışlara etkisini ölçmek
    # için). Varsayılan: hızlı bellek içi SQLite.
    if os.environ.get("TEST_DB_MODE") == "file":
        engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}")
    else:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def api_client(db_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)
