import asyncio
import time
from typing import Dict, Optional, Tuple

from app.ports.cache import CachePort


class InMemoryCacheAdapter(CachePort):
    """
    Single-process in-memory cache for local dev and tests.

    Supports optional TTL on scalar keys. Hash operations never expire.
    Thread-safe via asyncio.Lock — safe under concurrent coroutines.

    Not for production — state is process-local and lost on restart.
    """

    def __init__(self) -> None:
        # (value, expiry_monotonic | None)
        self._store: Dict[str, Tuple[str, Optional[float]]] = {}
        self._hstore: Dict[str, Dict[str, str]] = {}
        self._lock = asyncio.Lock()

    def _expired(self, expiry: Optional[float]) -> bool:
        return expiry is not None and time.monotonic() > expiry

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None or self._expired(entry[1]):
                return None
            return entry[0]

    async def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        expiry = time.monotonic() + ttl_seconds if ttl_seconds else None
        async with self._lock:
            self._store[key] = (value, expiry)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)
            self._hstore.pop(key, None)

    async def increment(self, key: str, amount: int = 1) -> int:
        async with self._lock:
            entry = self._store.get(key)
            current = int(entry[0]) if entry and not self._expired(entry[1]) else 0
            new_val = current + amount
            self._store[key] = (str(new_val), None)
            return new_val

    async def hget(self, key: str, field: str) -> Optional[str]:
        async with self._lock:
            return self._hstore.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: str) -> None:
        async with self._lock:
            self._hstore.setdefault(key, {})[field] = value

    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        async with self._lock:
            h = self._hstore.setdefault(key, {})
            new_val = int(h.get(field, 0)) + amount
            h[field] = str(new_val)
            return new_val

    async def hgetall(self, key: str) -> Dict[str, str]:
        async with self._lock:
            return dict(self._hstore.get(key, {}))

    async def reset(self) -> None:
        async with self._lock:
            self._store.clear()
            self._hstore.clear()

    async def close(self) -> None:
        await self.reset()
