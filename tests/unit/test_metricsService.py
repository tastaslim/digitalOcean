import pytest

from app.adapters.cache.memory import InMemoryCacheAdapter
from app.resources.metrics.metricsService import MetricsService


@pytest.fixture
def store() -> MetricsService:
    return MetricsService(cache=InMemoryCacheAdapter())


# ---------------------------------------------------------------------------
# incrementRequests
# ---------------------------------------------------------------------------


async def test_incrementRequests_once(store: MetricsService) -> None:
    await store.incrementRequests()
    snap = await store.snapshot()
    assert snap["totalRequests"] == 1


async def test_incrementRequests_multiple(store: MetricsService) -> None:
    for _ in range(5):
        await store.incrementRequests()
    snap = await store.snapshot()
    assert snap["totalRequests"] == 5


# ---------------------------------------------------------------------------
# recordShadowResult
# ---------------------------------------------------------------------------


async def test_recordShadowResult_error_increments_shadowErrors(store: MetricsService) -> None:
    await store.recordShadowResult(error=True)
    snap = await store.snapshot()
    assert snap["shadowCompleted"] == 1
    assert snap["shadowErrors"] == 1
    assert snap["exactMatchRatePct"] == 0.0


async def test_recordShadowResult_exactMatch_increments_exactMatches(store: MetricsService) -> None:
    await store.recordShadowResult(error=False, exactMatch=True)
    snap = await store.snapshot()
    assert snap["shadowCompleted"] == 1
    assert snap["exactMatchRatePct"] == 100.0
    assert snap["shadowErrors"] == 0


async def test_recordShadowResult_mismatch_only_increments_completed(store: MetricsService) -> None:
    await store.recordShadowResult(error=False, exactMatch=False)
    snap = await store.snapshot()
    assert snap["shadowCompleted"] == 1
    assert snap["exactMatchRatePct"] == 0.0
    assert snap["shadowErrors"] == 0


async def test_recordShadowResult_error_skips_exactMatch_flag(store: MetricsService) -> None:
    """error=True must win over exactMatch=True."""
    await store.recordShadowResult(error=True, exactMatch=True)
    snap = await store.snapshot()
    assert snap["shadowErrors"] == 1
    assert snap["exactMatchRatePct"] == 0.0


# ---------------------------------------------------------------------------
# recordShed
# ---------------------------------------------------------------------------


async def test_recordShed_increments(store: MetricsService) -> None:
    await store.recordShed()
    await store.recordShed()
    snap = await store.snapshot()
    assert snap["shedCount"] == 2


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


async def test_snapshot_zero_state(store: MetricsService) -> None:
    snap = await store.snapshot()
    assert snap["totalRequests"] == 0
    assert snap["shadowErrors"] == 0
    assert snap["shadowCompleted"] == 0
    assert snap["exactMatchRatePct"] == 0.0
    assert snap["shedCount"] == 0


async def test_snapshot_exactMatchRate_two_thirds(store: MetricsService) -> None:
    await store.recordShadowResult(error=False, exactMatch=True)
    await store.recordShadowResult(error=False, exactMatch=True)
    await store.recordShadowResult(error=False, exactMatch=False)
    snap = await store.snapshot()
    assert snap["exactMatchRatePct"] == pytest.approx(66.67)


async def test_snapshot_exactMatchRate_all_errors(store: MetricsService) -> None:
    await store.recordShadowResult(error=True)
    await store.recordShadowResult(error=True)
    snap = await store.snapshot()
    assert snap["exactMatchRatePct"] == 0.0


async def test_snapshot_exactMatchRate_100_percent(store: MetricsService) -> None:
    await store.recordShadowResult(error=False, exactMatch=True)
    snap = await store.snapshot()
    assert snap["exactMatchRatePct"] == 100.0
