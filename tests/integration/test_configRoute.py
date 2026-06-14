import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.main import app
from app.resources.config.configService import ConfigService
from app.resources.metrics.metricsService import MetricsService
from tests.conftest import CHAT_PAYLOAD, PRIMARY_RESPONSE

transport = httpx.ASGITransport(app=app)


@pytest.fixture
async def client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# PUT /config — validation
# ---------------------------------------------------------------------------


async def test_config_update_returns_200(client: httpx.AsyncClient) -> None:
    resp = await client.put("/config", json={"shadowPercentage": 50.0})
    assert resp.status_code == 200


async def test_config_update_returns_applied_value(client: httpx.AsyncClient) -> None:
    resp = await client.put("/config", json={"shadowPercentage": 75.0})
    data = resp.json()["data"]
    assert data["shadowPercentage"] == 75.0


async def test_config_update_persists_to_runtime(client: httpx.AsyncClient) -> None:
    await client.put("/config", json={"shadowPercentage": 25.0})
    svc = ConfigService(cache=app.state.container.cache)
    assert await svc.getShadowPercentage() == 25.0


async def test_config_rejects_percentage_above_100(client: httpx.AsyncClient) -> None:
    resp = await client.put("/config", json={"shadowPercentage": 101.0})
    assert resp.status_code == 400


async def test_config_rejects_negative_percentage(client: httpx.AsyncClient) -> None:
    resp = await client.put("/config", json={"shadowPercentage": -1.0})
    assert resp.status_code == 400


async def test_config_accepts_zero_percent(client: httpx.AsyncClient) -> None:
    resp = await client.put("/config", json={"shadowPercentage": 0.0})
    assert resp.status_code == 200


async def test_config_accepts_100_percent(client: httpx.AsyncClient) -> None:
    resp = await client.put("/config", json={"shadowPercentage": 100.0})
    assert resp.status_code == 200


async def test_config_rejects_missing_field(client: httpx.AsyncClient) -> None:
    resp = await client.put("/config", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Behavioural: shadow percentage gates shadow execution
# ---------------------------------------------------------------------------


async def test_zero_percent_suppresses_all_shadows(client: httpx.AsyncClient) -> None:
    await client.put("/config", json={"shadowPercentage": 0.0})

    with patch.object(app.state.container.primaryLlm, "chat", new_callable=AsyncMock) as mock:
        mock.return_value = PRIMARY_RESPONSE
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.05)

    assert mock.await_count == 1  # primary only — shadow never published
    svc = MetricsService(cache=app.state.container.cache)
    snap = await svc.snapshot()
    assert snap["shadowCompleted"] == 0


async def test_100_percent_always_shadows(client: httpx.AsyncClient) -> None:
    await client.put("/config", json={"shadowPercentage": 100.0})

    with (
        patch.object(app.state.container.primaryLlm, "chat", new_callable=AsyncMock) as primary_mock,
        patch.object(app.state.shadow_worker.candidateLlm, "chat", new_callable=AsyncMock) as shadow_mock,
    ):
        primary_mock.return_value = PRIMARY_RESPONSE
        shadow_mock.return_value = PRIMARY_RESPONSE
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    svc = MetricsService(cache=app.state.container.cache)
    snap = await svc.snapshot()
    assert snap["shadowCompleted"] == 1
