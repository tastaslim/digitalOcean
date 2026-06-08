import asyncio


class RuntimeConfig:
    """Mutable runtime configuration, updated live via PUT /config."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.shadowPercentage: float = 100.0

    async def update(self, shadowPercentage: float) -> None:
        """
        Replace the shadow routing percentage atomically.

        Args:
            shadowPercentage: Proportion of requests (0–100) to mirror to the candidate LLM.
        """
        async with self._lock:
            self.shadowPercentage = shadowPercentage

    def snapshot(self) -> dict[str, float]:
        """Return a point-in-time copy of the current runtime config."""
        return {"shadowPercentage": self.shadowPercentage}


runtimeConfig = RuntimeConfig()
