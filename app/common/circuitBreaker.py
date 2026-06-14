import asyncio
import logging
import time
from enum import Enum
from typing import Any, Awaitable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"       # normal — all calls pass through
    OPEN = "open"           # failing fast — calls rejected immediately
    HALF_OPEN = "half_open" # one probe allowed to test recovery


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""


class CircuitBreaker:
    """
    Classic three-state circuit breaker for an async call target.

    State transitions:
      CLOSED  → OPEN      after `failureThreshold` consecutive failures
      OPEN    → HALF_OPEN after `recoveryTimeoutSeconds` have elapsed
      HALF_OPEN → CLOSED  on the next successful call
      HALF_OPEN → OPEN    on the next failed call

    Hot-path optimisation:
      The lock is NOT acquired on the happy path (CLOSED state, zero failures).
      In asyncio (single-threaded) reads of Python object attributes are already
      atomic between await points, so a lockless pre-check is safe. The lock is
      only taken for actual state transitions, which are rare.

    Usage:
        result = await breaker.call(some_coroutine())
    """

    def __init__(
        self,
        failureThreshold: int = 5,
        recoveryTimeoutSeconds: int = 30,
    ) -> None:
        self._threshold = failureThreshold
        self._recoveryTimeout = recoveryTimeoutSeconds
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._openedAt: float = 0.0
        self._lock = asyncio.Lock()

    async def call(self, coro: Awaitable[Any]) -> Any:
        # ── Fast path: CLOSED with no prior failures ─────────────────────────
        # No lock needed — in asyncio, attribute reads between await points are
        # atomic. We re-check under lock only when the state is not CLOSED.
        if self._state != CircuitState.CLOSED:
            async with self._lock:
                if self._state == CircuitState.OPEN:
                    elapsed = time.monotonic() - self._openedAt
                    if elapsed >= self._recoveryTimeout:
                        self._state = CircuitState.HALF_OPEN
                        logger.warning("Circuit breaker → HALF_OPEN, probing recovery")
                    else:
                        remaining = int(self._recoveryTimeout - elapsed)
                        raise CircuitOpenError(
                            f"Primary LLM circuit is OPEN ({self._failures} consecutive "
                            f"failures). Retry in {remaining}s."
                        )

        # ── Execute the coroutine (lock NOT held) ────────────────────────────
        try:
            result = await coro
        except Exception:
            async with self._lock:
                self._failures += 1
                if self._failures >= self._threshold:
                    self._state = CircuitState.OPEN
                    self._openedAt = time.monotonic()
                    logger.error(
                        "Circuit breaker → OPEN after %d consecutive failures",
                        self._failures,
                    )
            raise

        # ── Success: reset only when there is state to clear ─────────────────
        # Skip the lock entirely when already CLOSED with zero failures —
        # that's the steady state for every well-behaved request.
        if self._failures != 0 or self._state != CircuitState.CLOSED:
            async with self._lock:
                if self._state == CircuitState.HALF_OPEN:
                    logger.info("Circuit breaker → CLOSED, primary LLM recovered")
                self._failures = 0
                self._state = CircuitState.CLOSED

        return result

    def reset(self) -> None:
        """Reset to CLOSED with zero failure count. Used in tests."""
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._openedAt = 0.0

    @property
    def state(self) -> str:
        return self._state.value
