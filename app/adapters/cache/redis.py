import logging
from typing import Dict, Optional
from redis.asyncio import Redis
from app.ports.cache import CachePort

logger = logging.getLogger(__name__)


class RedisAdapter(CachePort):
    """
    Redis-backed cache using the official async redis-py client.

    All operations are single-command (no pipelines needed here because each
    call site in the application issues one logical operation at a time).
    hincrby is atomic server-side so no application-level lock is required.

    Requires: pip install redis[asyncio]
    """

    def __init__(self, url: str) -> None:
        self._client = Redis.from_url(url, decode_responses=True)
        
    async def get(self, key: str) -> Optional[str]:
        return await self._client.get(key)

    async def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def increment(self, key: str, amount: int = 1) -> int:
        return await self._client.incrby(key, amount)

    async def hget(self, key: str, field: str) -> Optional[str]:
        return await self._client.hget(key, field)

    async def hset(self, key: str, field: str, value: str) -> None:
        await self._client.hset(key, field, value)

    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        return await self._client.hincrby(key, field, amount)

    async def hgetall(self, key: str) -> Dict[str, str]:
        return await self._client.hgetall(key)

    async def reset(self) -> None:
        await self._client.flushdb()

    async def close(self) -> None:
        await self._client.aclose()
