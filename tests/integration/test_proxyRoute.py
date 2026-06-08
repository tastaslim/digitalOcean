import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.main import app
from app.resources.metrics.metricsService import metricsStore
from tests.conftest import (
    CANDIDATE_RESPONSE_INVALID_JSON,
    CANDIDATE_RESPONSE_MATCH,
    CANDIDATE_RESPONSE_MISMATCH,
    CHAT_PAYLOAD,
    PRIMARY_RESPONSE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

transport = httpx.ASGITransport(app=app)


@pytest.fixture
async def client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


async def test_chat_returns_primary_response(client: httpx.AsyncClient) -> None:
    with patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock:
        mock.side_effect = [PRIMARY_RESPONSE, CANDIDATE_RESPONSE_MATCH]
        resp = await client.post("/v1/chat", json=CHAT_PAYLOAD)

    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == '{"action": "buy"}'


async def test_chat_increments_total_requests(client: httpx.AsyncClient) -> None:
    with patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock:
        mock.side_effect = [PRIMARY_RESPONSE, CANDIDATE_RESPONSE_MATCH]
        await client.post("/v1/chat", json=CHAT_PAYLOAD)

    assert metricsStore.totalRequests == 1


# ---------------------------------------------------------------------------
# Shadow execution and metrics
# ---------------------------------------------------------------------------


async def test_shadow_fires_and_records_exact_match(client: httpx.AsyncClient) -> None:
    with patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock:
        mock.side_effect = [PRIMARY_RESPONSE, CANDIDATE_RESPONSE_MATCH]
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    assert metricsStore.shadowCompleted == 1
    assert metricsStore.exactMatches == 1


async def test_shadow_records_mismatch_to_sqlite(client: httpx.AsyncClient) -> None:
    with (
        patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock,
        patch("app.resources.proxy.proxyService.recordMismatch", new_callable=AsyncMock) as dbMock,
    ):
        mock.side_effect = [PRIMARY_RESPONSE, CANDIDATE_RESPONSE_MISMATCH]
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    dbMock.assert_awaited_once()
    call_kwargs = dbMock.call_args.kwargs
    assert call_kwargs["primaryAction"] == "buy"
    assert call_kwargs["candidateAction"] == "sell"


async def test_shadow_does_not_record_mismatch_when_exact_match(client: httpx.AsyncClient) -> None:
    with (
        patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock,
        patch("app.resources.proxy.proxyService.recordMismatch", new_callable=AsyncMock) as dbMock,
    ):
        mock.side_effect = [PRIMARY_RESPONSE, CANDIDATE_RESPONSE_MATCH]
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    dbMock.assert_not_awaited()


async def test_shadow_invalid_json_does_not_count_as_match(client: httpx.AsyncClient) -> None:
    with patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock:
        mock.side_effect = [PRIMARY_RESPONSE, CANDIDATE_RESPONSE_INVALID_JSON]
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    assert metricsStore.exactMatches == 0
    assert metricsStore.shadowErrors == 0  # not an error — just not valid JSON


async def test_shadow_error_increments_shadow_errors(client: httpx.AsyncClient) -> None:
    with patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock:
        mock.side_effect = [PRIMARY_RESPONSE, Exception("candidate timed out")]
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.1)

    assert metricsStore.shadowErrors == 1


# ---------------------------------------------------------------------------
# Load shedding
# ---------------------------------------------------------------------------


async def test_shadow_shed_when_pool_full(client: httpx.AsyncClient) -> None:
    from app.resources.proxy.proxyService import shadowPool

    shadowPool._active = shadowPool._maxConcurrent  # simulate full pool

    with patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock:
        mock.return_value = PRIMARY_RESPONSE
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.05)

    assert metricsStore.shedCount == 1
    assert metricsStore.shadowCompleted == 0

    shadowPool._active = 0  # restore


# ---------------------------------------------------------------------------
# Shadow percentage (runtimeConfig)
# ---------------------------------------------------------------------------


async def test_shadow_skipped_at_zero_percent(client: httpx.AsyncClient) -> None:
    from app.resources.config.configService import runtimeConfig

    runtimeConfig.shadowPercentage = 0.0

    with patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock:
        mock.return_value = PRIMARY_RESPONSE
        await client.post("/v1/chat", json=CHAT_PAYLOAD)
        await asyncio.sleep(0.05)

    assert metricsStore.shadowCompleted == 0
    # _callLlm called exactly once (primary only)
    assert mock.await_count == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_chat_primary_http_error_returns_upstream_status(client: httpx.AsyncClient) -> None:
    errorResponse = httpx.Response(422, text="Unprocessable")
    with patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock:
        mock.side_effect = httpx.HTTPStatusError(
            "422", request=httpx.Request("POST", "http://llm"), response=errorResponse
        )
        resp = await client.post("/v1/chat", json=CHAT_PAYLOAD)

    assert resp.status_code == 422


async def test_chat_primary_request_error_returns_502(client: httpx.AsyncClient) -> None:
    with patch("app.resources.proxy.proxyService._callLlm", new_callable=AsyncMock) as mock:
        mock.side_effect = httpx.RequestError("connection refused")
        resp = await client.post("/v1/chat", json=CHAT_PAYLOAD)

    assert resp.status_code == 502


async def test_chat_missing_messages_returns_400(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/chat", json={})
    assert resp.status_code == 400
