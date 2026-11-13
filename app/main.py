from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.deps import enforce_default_policy
from app.auth.router import router as auth_router
from app.bank.router import router as bank_router
from app.chart.router import router as chart_router
from app.core.config import get_settings
from app.core.db import engine
from app.core.models import Base
from app.core.security_headers import SecurityHeadersMiddleware
from app.documents.router import router as documents_router
from app.einvoice.router import router as einvoice_router
from app.erp.router import router as erp_router
from app.learning.router import router as learning_router
from app.ledger.router import router as ledger_router
from app.tax.router import router as tax_router
from app.web.router import router as web_router

# Ayarlar import anında doğrulanır: AUTH_SECRET yoksa/kısaysa uygulama açılmaz.
settings = get_settings()

# Kimlik doğrulamasız erişilebilen yollar. Bu liste dışındaki her API yolu
# token ister; tests/test_route_auth.py bunu tüm route'ları tarayarak doğrular.
PUBLIC_PATHS: frozenset[str] = frozenset({"/health", "/auth/login", "/app"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Faz 1'de Alembic migration'larıyla değiştirilecek. Varsayılan hesap
    # oluşturulmaz; ilk yönetici `python -m app.cli create-admin` ile açılır.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


IS_PRODUCTION = settings.app_env == "production"

# Production'da API şeması ve etkileşimli dokümantasyon kapatılır.
app = FastAPI(
    title=settings.app_name,
    version="0.3.0",
    lifespan=lifespan,
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)
app.add_middleware(SecurityHeadersMiddleware, production=IS_PRODUCTION)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "web" / "static"),
    name="static",
)

# /auth uçları kendi bağımlılıklarını tek tek tanımlar (login herkese açık).
app.include_router(auth_router)

# Veri router'ları: router seviyesinde zorunlu kimlik doğrulama + varsayılan rol
# politikası (okuma tüm rollere, yazma mükellef rolü hariç). Hassas uçlar ek
# olarak kendi rol bağımlılıklarını taşır.
_protected = [Depends(enforce_default_policy)]
for data_router in (
    documents_router,
    bank_router,
    erp_router,
    einvoice_router,
    ledger_router,
    tax_router,
    learning_router,
    chart_router,
):
    app.include_router(data_router, dependencies=_protected)

# Yalnızca statik panel HTML'i; veri çağrılarının hepsi token ister.
app.include_router(web_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
