import asyncio
from collections.abc import Coroutine
from typing import Any


class ShadowPool:
    """
    Bounded pool for fire-and-forget shadow evaluation tasks.

    Tasks beyond *maxConcurrent* are dropped (load-shed) rather than queued,
    preventing unbounded memory growth under traffic bursts.  The active slot
    is reserved inside the lock before the task is created, so concurrent
    submits cannot over-commit capacity.

    :param maxConcurrent: Maximum number of shadow tasks allowed to run simultaneously.
    :type maxConcurrent: int
    """

    def __init__(self, maxConcurrent: int) -> None:
        """
        :param maxConcurrent: Maximum simultaneous shadow tasks allowed to run.
        :type maxConcurrent: int
        """
        self._maxConcurrent = maxConcurrent
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        """
        Current number of in-flight shadow tasks.

        :return: Count of actively running shadow coroutines.
        :rtype: int
        """
        return self._active

    async def submit(self, coro: Coroutine[Any, Any, None], name: str | None = None) -> bool:
        """
        Accept or reject a shadow coroutine based on current pool capacity.

        The slot is reserved atomically before task creation so concurrent
        submits cannot both pass the capacity check.

        :param coro: Shadow coroutine to run in the background.
        :type coro: Coroutine[Any, Any, None]
        :return: ``True`` if the task was accepted and scheduled;
                 ``False`` if the pool was at capacity (coroutine is closed
                 immediately to prevent ``ResourceWarning``).
        :rtype: bool
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

        asyncio.create_task(_wrap(), name=name or "shadow-eval")
        return True
