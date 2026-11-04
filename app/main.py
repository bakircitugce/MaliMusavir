from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.db import engine, session_factory
from app.core.models import Base
from app.auth.service import ensure_default_accounts
from app.auth.router import router as auth_router
from app.documents.router import router as documents_router
from app.bank.router import router as bank_router
from app.erp.router import router as erp_router
from app.tax.router import router as tax_router
from app.learning.router import router as learning_router
from app.einvoice.router import router as einvoice_router
from app.ledger.router import router as ledger_router
from app.web.router import router as web_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-initialize database tables and baseline accounts on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        await ensure_default_accounts(session)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "web" / "static"),
    name="static",
)
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(bank_router)
app.include_router(erp_router)
app.include_router(einvoice_router)
app.include_router(ledger_router)
app.include_router(tax_router)
app.include_router(learning_router)
app.include_router(web_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}

