import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.domain.models.enums import TaskType
from app.main import app
from app.resources.metrics.metricsService import MetricsService
from tests.conftest import (
    CANDIDATE_RESPONSE_INVALID_JSON,
    CANDIDATE_RESPONSE_MATCH,
    CANDIDATE_RESPONSE_MISMATCH,
    CHAT_PAYLOAD,
    PRIMARY_RESPONSE,
)

transport = httpx.ASGITransport(app=app)

# Proxy publishes to queue; worker calls the candidate LLM independently.
# Mock at the adapter instance level so the two providers are fully decoupled.
# app.state is populated by the session fixture in conftest.py before any test runs.


@pytest.fixture
async def client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _snap() -> dict:
    svc = MetricsService(cache=app.state.container.cache)
    return await svc.snapshot()


def _primaryMock(**kwargs):
    """patch.object context manager for the primary LLM adapter's chat method."""
    return patch.object(app.state.container.primaryLlm, "chat", new_callable=AsyncMock, **kwargs)


def _shadowMock(**kwargs):
    """patch.object context manager for the candidate LLM adapter's chat method."""
    return patch.object(app.state.shadow_worker.candidateLlm, "chat", new_callable=AsyncMock, **kwargs)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


async def test_chat_returns_primary_response(client: httpx.AsyncClient) -> None:
    with _primaryMock() as mock:
        mock.return_value = PRIMARY_RESPONSE
        resp = await client.post("/v1/chat", json=CHAT_PAYLOAD)

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == '{"action": "buy"}'


async def test_chat_increments_total_requests(client: httpx.AsyncClient) -> None:
    with _primaryMock() as mock:
        mock.return_value = PRIMARY_RESPONSE
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        # incrementRequests is a background task; yield to let it run
        await asyncio.sleep(0)

    assert (await _snap())["totalRequests"] == 1


# ---------------------------------------------------------------------------
# Shadow execution and metrics
# ---------------------------------------------------------------------------


async def test_shadow_fires_and_records_exact_match(client: httpx.AsyncClient) -> None:
    with _primaryMock() as p, _shadowMock() as s:
        p.return_value = PRIMARY_RESPONSE
        s.return_value = CANDIDATE_RESPONSE_MATCH
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    snap = await _snap()
    assert snap["shadowCompleted"] == 1
    assert snap["exactMatchRatePct"] == 100.0


async def test_shadow_records_mismatch_to_db(client: httpx.AsyncClient) -> None:
    with _primaryMock() as p, _shadowMock() as s:
        p.return_value = PRIMARY_RESPONSE
        s.return_value = CANDIDATE_RESPONSE_MISMATCH
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    records = await app.state.container.mismatchRepository.findByTaskType(
        TaskType.GENERIC
    )
    assert len(records) == 1
    assert records[0].primaryAction == "buy"
    assert records[0].candidateAction == "sell"


async def test_shadow_does_not_record_mismatch_when_exact_match(
    client: httpx.AsyncClient,
) -> None:
    with _primaryMock() as p, _shadowMock() as s:
        p.return_value = PRIMARY_RESPONSE
        s.return_value = CANDIDATE_RESPONSE_MATCH
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    records = await app.state.container.mismatchRepository.findByTaskType(
        TaskType.GENERIC
    )
    assert len(records) == 0


async def test_shadow_invalid_json_does_not_count_as_match(
    client: httpx.AsyncClient,
) -> None:
    with _primaryMock() as p, _shadowMock() as s:
        p.return_value = PRIMARY_RESPONSE
        s.return_value = CANDIDATE_RESPONSE_INVALID_JSON
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    snap = await _snap()
    assert snap["exactMatchRatePct"] == 0.0
    assert snap["shadowErrors"] == 0  # parse failure ≠ error


async def test_shadow_error_increments_shadow_errors(client: httpx.AsyncClient) -> None:
    with _primaryMock() as p, _shadowMock() as s:
        p.return_value = PRIMARY_RESPONSE
        s.side_effect = Exception("candidate timed out")
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    assert (await _snap())["shadowErrors"] == 1


# ---------------------------------------------------------------------------
# Load shedding — pool is full; worker records shed instead of evaluating
# ---------------------------------------------------------------------------


async def test_shadow_shed_when_pool_full(client: httpx.AsyncClient) -> None:
    worker = app.state.shadow_worker
    worker.pool._active = worker.pool._maxConcurrent  # simulate a full pool

    with _primaryMock() as mock:
        mock.return_value = PRIMARY_RESPONSE
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    snap = await _snap()
    assert snap["shedCount"] == 1
    assert snap["shadowCompleted"] == 0

    worker.pool._active = 0  # restore


# ---------------------------------------------------------------------------
# Shadow percentage — already validated in test_configRoute; spot-check here
# ---------------------------------------------------------------------------


async def test_shadow_skipped_at_zero_percent(client: httpx.AsyncClient) -> None:
    await client.put("/config", json={"shadowPercentage": 0.0})

    with _primaryMock() as mock:
        mock.return_value = PRIMARY_RESPONSE
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.05)

    assert mock.await_count == 1  # primary only
    assert (await _snap())["shadowCompleted"] == 0


# ---------------------------------------------------------------------------
# Error handling — primary LLM failures forwarded to client
# ---------------------------------------------------------------------------


async def test_chat_primary_http_error_returns_upstream_status(
    client: httpx.AsyncClient,
) -> None:
    error_response = httpx.Response(422, text="Unprocessable")
    with _primaryMock() as mock:
        mock.side_effect = httpx.HTTPStatusError(
            "422",
            request=httpx.Request("POST", "http://llm"),
            response=error_response,
        )
        resp = await client.post("/v1/chat", json=CHAT_PAYLOAD)

    assert resp.status_code == 422


async def test_chat_primary_request_error_returns_502(client: httpx.AsyncClient) -> None:
    with _primaryMock() as mock:
        mock.side_effect = httpx.RequestError("connection refused")
        resp = await client.post("/v1/chat", json=CHAT_PAYLOAD)

    assert resp.status_code == 502


async def test_chat_missing_messages_returns_400(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/chat", json={})
    assert resp.status_code == 400
