from abc import ABC, abstractmethod
from typing import Dict, Optional


class CachePort(ABC):
    """
    Port for key-value caching with atomic counters and hash support.

    Implementations: InMemoryCacheAdapter (dev/test), RedisAdapter.

    All methods are async so implementations can use non-blocking I/O.
    """

    @abstractmethod
    async def get(self, key: str) -> Optional[str]: ...

    @abstractmethod
    async def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def increment(self, key: str, amount: int = 1) -> int:
        """Atomically increment an integer counter. Creates key at 0 if absent."""

    @abstractmethod
    async def hget(self, key: str, field: str) -> Optional[str]: ...

    @abstractmethod
    async def hset(self, key: str, field: str, value: str) -> None: ...

    @abstractmethod
    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        """Atomically increment a field inside a hash. Creates field at 0 if absent."""

    @abstractmethod
    async def hgetall(self, key: str) -> Dict[str, str]: ...

    @abstractmethod
    async def reset(self) -> None:
        """Clear all stored state. Intended for tests only."""

    @abstractmethod
    async def close(self) -> None: ...
