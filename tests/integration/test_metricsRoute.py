import httpx
import pytest

from app.main import app
from app.resources.metrics.metricsService import MetricsService

transport = httpx.ASGITransport(app=app)


@pytest.fixture
async def client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_metrics_returns_200(client: httpx.AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200


async def test_metrics_response_has_api_response_envelope(client: httpx.AsyncClient) -> None:
    resp = await client.get("/metrics")
    body = resp.json()
    assert body["status"] == 200
    assert "data" in body
    assert "message" in body


async def test_metrics_keys_are_camel_case(client: httpx.AsyncClient) -> None:
    resp = await client.get("/metrics")
    data = resp.json()["data"]
    expected = {"totalRequests", "shadowErrors", "shadowCompleted", "exactMatchRatePct", "shedCount"}
    assert set(data.keys()) == expected


async def test_metrics_initial_state_is_zeroed(client: httpx.AsyncClient) -> None:
    resp = await client.get("/metrics")
    data = resp.json()["data"]
    assert data["totalRequests"] == 0
    assert data["shadowCompleted"] == 0
    assert data["exactMatchRatePct"] == 0.0
    assert data["shedCount"] == 0


async def test_metrics_reflect_incremented_counters(client: httpx.AsyncClient) -> None:
    svc = MetricsService(cache=app.state.container.cache)
    for _ in range(7):
        await svc.incrementRequests()
    for _ in range(6):
        await svc.recordShadowResult(error=False, exactMatch=False)
    for _ in range(4):
        await svc.recordShadowResult(error=False, exactMatch=True)
    await svc.recordShed()

    resp = await client.get("/metrics")
    data = resp.json()["data"]
    assert data["totalRequests"] == 7
    assert data["shadowCompleted"] == 10
    assert data["shedCount"] == 1
    assert data["exactMatchRatePct"] == pytest.approx(40.0)
