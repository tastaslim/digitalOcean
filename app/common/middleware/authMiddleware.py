from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Paths that never require authentication.
_EXEMPT: frozenset[str] = frozenset({
    "/health",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
})


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """
    Validates the X-API-Key header on every non-exempt request.

    When PROXY_API_KEY is empty (default in cloud.env), auth is disabled
    entirely — no header is required. This keeps local dev and the test
    suite working without configuration changes.

    In production, set PROXY_API_KEY to a strong random secret and require
    all clients to send:   X-API-Key: <secret>
    """

    def __init__(self, app, apiKey: str) -> None:
        super().__init__(app)
        self._apiKey = apiKey

    async def dispatch(self, request: Request, call_next):
        if not self._apiKey:
            return await call_next(request)
        if request.url.path in _EXEMPT:
            return await call_next(request)
        if request.headers.get("X-API-Key") != self._apiKey:
            return JSONResponse(
                {"status": 401, "message": "Missing or invalid X-API-Key header"},
                status_code=401,
            )
        return await call_next(request)
