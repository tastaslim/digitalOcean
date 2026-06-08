import asyncio


class RuntimeConfig:
    """
    Mutable runtime configuration updated live via ``PUT /config``.

    All mutations go through :meth:`update` which holds an ``asyncio.Lock``,
    making writes safe under concurrent requests.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.shadowPercentage: float = 100.0

    async def update(self, shadowPercentage: float) -> None:
        """
        Replace the shadow routing percentage atomically.

        :param shadowPercentage: Proportion of requests (0–100) to mirror to
            the candidate LLM.
        :type shadowPercentage: float
        """
        async with self._lock:
            self.shadowPercentage = shadowPercentage

    def snapshot(self) -> dict[str, float]:
        """
        Return a point-in-time copy of the current runtime configuration.

        :return: Dict with key ``shadowPercentage`` reflecting the current value.
        :rtype: dict[str, float]
        """
        return {"shadowPercentage": self.shadowPercentage}


runtimeConfig = RuntimeConfig()
