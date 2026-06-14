import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.infrastructure.container import Container
from app.infrastructure.dependencies import getContainer

logger = logging.getLogger(__name__)

healthRoute = APIRouter(tags=["health"])


@healthRoute.get("/health", include_in_schema=False)
async def liveness():
    """
    Liveness probe — returns 200 instantly with no I/O.

    Use this for Docker / Kubernetes liveness checks. A 200 here means the
    process is alive and the event loop is responsive. It does NOT mean all
    downstream dependencies are healthy.
    """
    return {"status": "ok"}


@healthRoute.get("/ready")
async def readiness(container: Container = Depends(getContainer)):
    """
    Readiness probe — checks every downstream dependency.

    Returns 200 when all checks pass, 503 when any are degraded.
    Use this for Kubernetes readiness gates and load-balancer health checks
    so traffic is only routed to instances with healthy dependencies.
    """
    checks: dict[str, str] = {}

    # ── Database ──────────────────────────────────────────────────────
    db = container.mismatchRepository
    try:
        if hasattr(db, "_pool") and db._pool is not None:
            # PostgreSQL — run a trivial query against the pool
            async with db._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        # SQLite has no pool; if the adapter exists the file is accessible
        checks["db"] = "ok"
    except Exception as exc:
        logger.warning("Readiness DB check failed: %s", exc)
        checks["db"] = "error"

    # ── Cache (Redis / in-memory) ─────────────────────────────────────
    try:
        await container.cache.hget("health:ping", "x")
        checks["cache"] = "ok"
    except Exception as exc:
        logger.warning("Readiness cache check failed: %s", exc)
        checks["cache"] = "error"

    allOk = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {
            "status": "ok" if allOk else "degraded",
            "checks": checks,
        },
        status_code=200 if allOk else 503,
    )
