import time

from app.ports.cache import CachePort

_CONFIG_HASH = "config:shadow"
_SHADOW_PCT_FIELD = "shadowPercentage"
_DEFAULT_SHADOW_PCT = 100.0
_CACHE_TTL = 5.0  # seconds — shadow percentage is read on every request


class ConfigService:
    """
    Mutable runtime configuration backed by CachePort.

    In production this is a Redis hash — a single PUT /config write propagates
    to all API pods within milliseconds. In dev/tests this is InMemoryCacheAdapter.

    getShadowPercentage() is called on every proxy request, so it uses a
    class-level in-process TTL cache (5 s) to avoid a Redis round-trip on
    every call at scale. The cache is invalidated immediately on update() so
    the change is visible to the next request in the same process without
    waiting for TTL expiry.
    """

    # Class-level cache — shared across all ConfigService instances in one process.
    # At scale, each pod has its own copy; Redis is the source of truth.
    _pctCache: float | None = None
    _pctCacheTs: float = 0.0

    def __init__(self, cache: CachePort) -> None:
        self._cache = cache

    async def update(self, shadowPercentage: float) -> None:
        await self._cache.hset(_CONFIG_HASH, _SHADOW_PCT_FIELD, str(shadowPercentage))
        ConfigService._pctCache = None  # invalidate so next read is fresh

    async def getShadowPercentage(self) -> float:
        now = time.monotonic()
        if (
            ConfigService._pctCache is not None
            and now - ConfigService._pctCacheTs < _CACHE_TTL
        ):
            return ConfigService._pctCache
        val = await self._cache.hget(_CONFIG_HASH, _SHADOW_PCT_FIELD)
        result = float(val) if val is not None else _DEFAULT_SHADOW_PCT
        ConfigService._pctCache = result
        ConfigService._pctCacheTs = now
        return result

    async def snapshot(self) -> dict[str, float]:
        return {"shadowPercentage": await self.getShadowPercentage()}

    @classmethod
    def clearCache(cls) -> None:
        """Force-expire the in-process cache. Used in tests between runs."""
        cls._pctCache = None
        cls._pctCacheTs = 0.0
