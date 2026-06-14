import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


def safeTask(
    coro: Awaitable,
    *,
    name: Optional[str] = None,
    onError: Optional[Callable[[Exception], Awaitable[None]]] = None,
) -> asyncio.Task:
    """
    Schedule a background coroutine as an asyncio Task with guaranteed
    exception visibility.

    Plain asyncio.create_task() swallows exceptions — if the coroutine raises
    and nothing awaits the task, Python logs "Task exception was never retrieved"
    to stderr and drops it silently. At millions of calls/day that means lost
    SQS publishes, lost S3 archives, and lost metrics with no alerting.

    safeTask wraps the coroutine so any exception is:
      1. logged at ERROR level with full traceback (always)
      2. forwarded to onError(exc) if provided (e.g. to increment an error counter)

    The task itself never raises to the caller — fire and forget is preserved.
    """
    async def _run() -> None:
        try:
            await coro
        except Exception as exc:
            logger.error(
                "Background task %r raised an unhandled exception: %s",
                name or getattr(coro, "__qualname__", repr(coro)),
                exc,
                exc_info=True,
            )
            if onError:
                try:
                    await onError(exc)
                except Exception:
                    pass

    return asyncio.create_task(_run(), name=name)
