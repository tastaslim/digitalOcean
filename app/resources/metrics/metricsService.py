import asyncio


class MetricsStore:
    """Thread-safe in-memory counters for proxy and shadow execution stats."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()
        self.totalRequests: int = 0
        self.shadowErrors: int = 0
        self.shadowCompleted: int = 0
        self.exactMatches: int = 0
        self.shedCount: int = 0

    async def incrementRequests(self) -> None:
        """Atomically increment the total request counter."""
        async with self._lock:
            self.totalRequests += 1

    async def recordShadowResult(self, *, error: bool, exactMatch: bool = False) -> None:
        """
        Atomically record the outcome of one shadow execution.

        Args:
            error: True if the candidate call failed or timed out.
            exactMatch: True if both models returned valid JSON and `action` keys matched.
        """
        async with self._lock:
            self.shadowCompleted += 1
            if error:
                self.shadowErrors += 1
            elif exactMatch:
                self.exactMatches += 1

    async def recordShed(self) -> None:
        """Atomically increment the load-shed counter."""
        async with self._lock:
            self.shedCount += 1

    def snapshot(self) -> dict[str, int | float]:
        """
        Return a point-in-time copy of all counters.

        Returns:
            Dict with totalRequests, shadowErrors, shadowCompleted,
            exactMatchRatePct, and shedCount.
        """
        matchRate: float = (
            round(self.exactMatches / self.shadowCompleted * 100, 2)
            if self.shadowCompleted > 0
            else 0.0
        )
        return {
            "totalRequests": self.totalRequests,
            "shadowErrors": self.shadowErrors,
            "shadowCompleted": self.shadowCompleted,
            "exactMatchRatePct": matchRate,
            "shedCount": self.shedCount,
        }


# Module-level singleton shared across the process lifetime.
metricsStore = MetricsStore()
