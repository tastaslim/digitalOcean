import asyncio
from collections.abc import Coroutine
from typing import Any


class ShadowPool:
    """
    Bounded pool for fire-and-forget shadow evaluation tasks.

    Tasks beyond maxConcurrent are dropped (load-shed) rather than queued,
    preventing unbounded memory growth under traffic bursts. The active slot
    is reserved inside the lock before the task is created, so concurrent
    submits cannot over-commit capacity.
    """

    def __init__(self, maxConcurrent: int) -> None:
        """
        Args:
            maxConcurrent: Maximum simultaneous shadow tasks allowed to run.
        """
        self._maxConcurrent = maxConcurrent
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        """Current number of in-flight shadow tasks."""
        return self._active

    async def submit(self, coro: Coroutine[Any, Any, None]) -> bool:
        """
        Accept or reject a shadow coroutine based on current capacity.

        The slot is reserved atomically before task creation so concurrent
        submits cannot both pass the capacity check.

        Args:
            coro: Shadow coroutine to run in the background.

        Returns:
            True if the task was accepted and scheduled.
            False if the pool was at capacity (task is closed to prevent ResourceWarning).
        """
        async with self._lock:
            if self._active >= self._maxConcurrent:
                coro.close()
                return False
            self._active += 1

        async def _wrap() -> None:
            try:
                await coro
            finally:
                async with self._lock:
                    self._active -= 1

        asyncio.create_task(_wrap())
        return True
