"""HTTP güvenlik başlıkları (Faz 1).

- CSP: yalnızca kendi kaynaklarımız; satır içi script yok (panel olay dinleyicileri
  dashboard.js içinde). Stil için 'unsafe-inline' geçici olarak açıktır (panelde
  inline style öznitelikleri var).
- API yanıtları önbelleğe alınmaz (mali veri).
- HSTS yalnızca production'da (yerel HTTP geliştirmeyi bozmamak için).
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
# FastAPI'nin /docs sayfası CDN'den swagger-ui yükler; yalnızca geliştirmede açıktır.
DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "frame-ancestors 'none'"
)
_DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, production: bool) -> None:
        super().__init__(app)
        self.production = production

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        path = request.url.path
        headers = response.headers
        headers.setdefault(
            "Content-Security-Policy", DOCS_CSP if path.startswith(_DOCS_PATHS) else CSP
        )
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
        )
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if not path.startswith("/static"):
            headers.setdefault("Cache-Control", "no-store")
        if self.production:
            headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
