import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


def safeTask(
    coro: Awaitable,
    *,
    name: Optional[str] = None,
    onError: Optional[Callable[[Exception], Awaitable[None]]] = None,
    timeoutSeconds: Optional[float] = None,
) -> asyncio.Task:
    """
    Schedule a background coroutine as an asyncio Task with guaranteed
    exception visibility and an optional hard deadline.

    Plain asyncio.create_task() swallows exceptions — if the coroutine raises
    and nothing awaits the task, Python logs "Task exception was never retrieved"
    to stderr and drops it silently. At millions of calls/day that means lost
    SQS publishes, lost S3 archives, and lost metrics with no alerting.

    safeTask wraps the coroutine so any exception (including timeouts) is:
      1. logged at ERROR level with full traceback (always)
      2. forwarded to onError(exc) if provided (e.g. to increment a bgErrors counter)

    timeoutSeconds:
      If provided, wraps the coroutine with asyncio.wait_for so tasks that
      stall (SQS/S3 outage) don't pile up in the event loop indefinitely.
    """
    async def _run() -> None:
        try:
            if timeoutSeconds is not None:
                await asyncio.wait_for(coro, timeout=timeoutSeconds)
            else:
                await coro
        except Exception as exc:
            label = name or getattr(coro, "__qualname__", repr(coro))
            if isinstance(exc, asyncio.TimeoutError):
                logger.error(
                    "Background task %r timed out after %.1fs",
                    label,
                    timeoutSeconds,
                )
            else:
                logger.error(
                    "Background task %r raised an unhandled exception: %s",
                    label,
                    exc,
                    exc_info=True,
                )
            if onError:
                try:
                    await onError(exc)
                except Exception:
                    pass

    return asyncio.create_task(_run(), name=name)
