import pytest

from app.resources.metrics.metricsService import MetricsStore


@pytest.fixture
def store() -> MetricsStore:
    return MetricsStore()


# ---------------------------------------------------------------------------
# incrementRequests
# ---------------------------------------------------------------------------


async def test_incrementRequests_once(store: MetricsStore) -> None:
    await store.incrementRequests()
    assert store.totalRequests == 1


async def test_incrementRequests_multiple(store: MetricsStore) -> None:
    for _ in range(5):
        await store.incrementRequests()
    assert store.totalRequests == 5


# ---------------------------------------------------------------------------
# recordShadowResult
# ---------------------------------------------------------------------------


async def test_recordShadowResult_error_increments_shadowErrors(store: MetricsStore) -> None:
    await store.recordShadowResult(error=True)
    assert store.shadowCompleted == 1
    assert store.shadowErrors == 1
    assert store.exactMatches == 0


async def test_recordShadowResult_exactMatch_increments_exactMatches(store: MetricsStore) -> None:
    await store.recordShadowResult(error=False, exactMatch=True)
    assert store.shadowCompleted == 1
    assert store.exactMatches == 1
    assert store.shadowErrors == 0


async def test_recordShadowResult_mismatch_only_increments_completed(store: MetricsStore) -> None:
    await store.recordShadowResult(error=False, exactMatch=False)
    assert store.shadowCompleted == 1
    assert store.exactMatches == 0
    assert store.shadowErrors == 0


async def test_recordShadowResult_error_skips_exactMatch_flag(store: MetricsStore) -> None:
    """error=True must win over exactMatch=True."""
    await store.recordShadowResult(error=True, exactMatch=True)
    assert store.shadowErrors == 1
    assert store.exactMatches == 0


# ---------------------------------------------------------------------------
# recordShed
# ---------------------------------------------------------------------------


async def test_recordShed_increments(store: MetricsStore) -> None:
    await store.recordShed()
    await store.recordShed()
    assert store.shedCount == 2


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def test_snapshot_zero_state(store: MetricsStore) -> None:
    snap = store.snapshot()
    assert snap["totalRequests"] == 0
    assert snap["shadowErrors"] == 0
    assert snap["shadowCompleted"] == 0
    assert snap["exactMatchRatePct"] == 0.0
    assert snap["shedCount"] == 0


async def test_snapshot_exactMatchRate_two_thirds(store: MetricsStore) -> None:
    await store.recordShadowResult(error=False, exactMatch=True)
    await store.recordShadowResult(error=False, exactMatch=True)
    await store.recordShadowResult(error=False, exactMatch=False)
    snap = store.snapshot()
    assert snap["exactMatchRatePct"] == pytest.approx(66.67)


async def test_snapshot_exactMatchRate_all_errors(store: MetricsStore) -> None:
    await store.recordShadowResult(error=True)
    await store.recordShadowResult(error=True)
    snap = store.snapshot()
    assert snap["exactMatchRatePct"] == 0.0


async def test_snapshot_exactMatchRate_100_percent(store: MetricsStore) -> None:
    await store.recordShadowResult(error=False, exactMatch=True)
    snap = store.snapshot()
    assert snap["exactMatchRatePct"] == 100.0
