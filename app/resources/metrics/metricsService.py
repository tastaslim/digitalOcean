from app.ports.cache import CachePort

_METRICS_HASH = "metrics:counters"


class MetricsService:
    """
    Proxy and shadow execution counters backed by CachePort.

    In production this is a Redis hash — all API pods share the same counters
    with atomic HINCRBY. In dev/tests this is an InMemoryCacheAdapter.

    All methods are async so they work identically across backends.
    """

    def __init__(self, cache: CachePort) -> None:
        self._cache = cache

    async def incrementRequests(self) -> None:
        await self._cache.hincrby(_METRICS_HASH, "totalRequests")

    async def recordShadowResult(self, *, error: bool, exactMatch: bool = False) -> None:
        await self._cache.hincrby(_METRICS_HASH, "shadowCompleted")
        if error:
            await self._cache.hincrby(_METRICS_HASH, "shadowErrors")
        elif exactMatch:
            await self._cache.hincrby(_METRICS_HASH, "exactMatches")

    async def recordShed(self) -> None:
        await self._cache.hincrby(_METRICS_HASH, "shedCount")

    async def snapshot(self) -> dict[str, int | float]:
        raw = await self._cache.hgetall(_METRICS_HASH)
        total = int(raw.get("totalRequests", 0))
        shadow_completed = int(raw.get("shadowCompleted", 0))
        exact_matches = int(raw.get("exactMatches", 0))
        shadow_errors = int(raw.get("shadowErrors", 0))
        shed_count = int(raw.get("shedCount", 0))
        match_rate = (
            round(exact_matches / shadow_completed * 100, 2)
            if shadow_completed > 0
            else 0.0
        )
        return {
            "totalRequests": total,
            "shadowErrors": shadow_errors,
            "shadowCompleted": shadow_completed,
            "exactMatchRatePct": match_rate,
            "shedCount": shed_count,
        }
