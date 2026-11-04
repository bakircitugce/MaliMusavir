from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["web"])
WEB_DIR = Path(__file__).parent


@router.get("/app", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(WEB_DIR / "dashboard.html")
