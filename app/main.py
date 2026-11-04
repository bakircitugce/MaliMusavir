from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.documents.router import router as documents_router
from app.bank.router import router as bank_router
from app.erp.router import router as erp_router
from app.tax.router import router as tax_router
from app.learning.router import router as learning_router
from app.web.router import router as web_router

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "web" / "static"),
    name="static",
)
app.include_router(documents_router)
app.include_router(bank_router)
app.include_router(erp_router)
app.include_router(tax_router)
app.include_router(learning_router)
app.include_router(web_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
