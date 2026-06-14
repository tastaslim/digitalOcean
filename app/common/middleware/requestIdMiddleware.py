import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Module-level ContextVar — set per-request, readable anywhere in the call stack
# (loggers, services, workers) without passing it explicitly.
REQUEST_ID_VAR: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Assigns a unique ID to every incoming request.

    Reads X-Request-ID from the incoming headers so callers can supply their own
    trace ID (useful when this service sits behind an API gateway that already
    stamps requests). Falls back to a fresh UUID4 if the header is absent.

    The ID is:
    - stored in REQUEST_ID_VAR so every log line in the same request includes it
    - echoed back in the X-Request-ID response header so clients can correlate
    """

    async def dispatch(self, request: Request, call_next):
        reqId = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = REQUEST_ID_VAR.set(reqId)
        try:
            response = await call_next(request)
        finally:
            REQUEST_ID_VAR.reset(token)
        response.headers["X-Request-ID"] = reqId
        return response
