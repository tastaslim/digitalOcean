import asyncio
import json
import logging
import random
from typing import Any

import httpx

from app.core.database import recordMismatch
from app.core.shadowPool import ShadowPool
from app.db.settings import getSettings
from app.resources.config.configService import runtimeConfig
from app.resources.metrics.metricsService import metricsStore

logger = logging.getLogger(__name__)
settings = getSettings()

_PRIMARY_CHAT_URL: str = f"{settings.PRIMARY_LLM_BASE_URL}/chat/completions"
_CANDIDATE_CHAT_URL: str = f"{settings.CANDIDATE_LLM_BASE_URL}/chat/completions"

# Bounded pool — shadows beyond MAX_CONCURRENT_SHADOWS are dropped, not queued.
shadowPool = ShadowPool(maxConcurrent=settings.MAX_CONCURRENT_SHADOWS)


def _authHeaders(apiKey: str) -> dict[str, str]:
    """
    Build Authorization and Content-Type headers for an LLM request.

    Args:
        apiKey: Bearer token for the target endpoint.

    Returns:
        Dict containing Authorization and Content-Type headers.
    """
    return {"Authorization": f"Bearer {apiKey}", "Content-Type": "application/json"}


async def _callLlm(
    client: httpx.AsyncClient,
    url: str,
    apiKey: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    POST a chat completion payload to an LLM endpoint and return the parsed response.

    Args:
        client: Shared async HTTP client for the request.
        url: Full chat completions URL of the target endpoint.
        apiKey: Bearer token for authentication.
        payload: OpenAI-compatible request body.

    Returns:
        Parsed JSON response from the LLM.

    Raises:
        httpx.HTTPStatusError: If the endpoint returns a non-2xx status.
        httpx.RequestError: If the request fails at the transport layer.
    """
    response = await client.post(url, headers=_authHeaders(apiKey), json=payload, timeout=60.0)
    response.raise_for_status()
    return response.json()


def _getContent(llmResponse: dict[str, Any]) -> str:
    """
    Extract the raw content string from the first choice of an LLM response.

    Args:
        llmResponse: Raw LLM response dict (OpenAI-compatible schema).

    Returns:
        Content string, or empty string if the path is missing.
    """
    try:
        return llmResponse["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""


def _extractAction(llmResponse: dict[str, Any]) -> str | None:
    """
    Parse the first choice's content as JSON and extract the `action` key.

    Args:
        llmResponse: Raw LLM response dict (OpenAI-compatible schema).

    Returns:
        The value of the `action` key if present and parseable, otherwise None.
    """
    try:
        content: str = llmResponse["choices"][0]["message"]["content"]
        return json.loads(content).get("action")
    except (KeyError, IndexError, json.JSONDecodeError, TypeError, AttributeError):
        return None


async def _runShadow(candidatePayload: dict[str, Any], primaryResponse: dict[str, Any]) -> None:
    """
    Fire-and-forget coroutine: call the candidate LLM, evaluate against the primary
    response, stream any mismatch to SQLite, and update metricsStore. Never raises.

    Args:
        candidatePayload: Request body to send to the candidate endpoint.
        primaryResponse: Already-returned primary LLM response used for comparison.
    """
    try:
        async with httpx.AsyncClient() as client:
            candidateResponse = await asyncio.wait_for(
                _callLlm(client, _CANDIDATE_CHAT_URL, settings.candidateKey(), candidatePayload),
                timeout=settings.SHADOW_TIMEOUT_SECONDS,
            )

        primaryAction = _extractAction(primaryResponse)
        candidateAction = _extractAction(candidateResponse)

        # Heuristic 1: both must yield parseable JSON (non-None implies JSON parsed).
        # Heuristic 2: the `action` key must match exactly.
        bothValid = primaryAction is not None and candidateAction is not None
        exactMatch = bothValid and primaryAction == candidateAction

        if bothValid and not exactMatch:
            await recordMismatch(
                primaryAction=primaryAction,
                candidateAction=candidateAction,
                primaryContent=_getContent(primaryResponse),
                candidateContent=_getContent(candidateResponse),
            )

        logger.debug(
            "Shadow eval — primaryAction=%r candidateAction=%r exactMatch=%s",
            primaryAction,
            candidateAction,
            exactMatch,
        )
        await metricsStore.recordShadowResult(error=False, exactMatch=exactMatch)

    except Exception as exc:
        logger.warning("Shadow execution failed: %s", exc)
        await metricsStore.recordShadowResult(error=True)


async def proxyChat(requestPayload: dict[str, Any]) -> dict[str, Any]:
    """
    Route the request to the primary LLM and immediately return its response.
    Conditionally dispatches the same request to the candidate for shadow evaluation
    based on runtimeConfig.shadowPercentage, subject to pool capacity.

    Args:
        requestPayload: OpenAI-compatible chat request body (model key excluded).

    Returns:
        The primary LLM's raw response dict.
    """
    await metricsStore.incrementRequests()

    primaryPayload = {**requestPayload, "model": settings.PRIMARY_LLM_MODEL}
    async with httpx.AsyncClient() as client:
        primaryResponse = await _callLlm(
            client, _PRIMARY_CHAT_URL, settings.primaryKey(), primaryPayload
        )

    if random.random() * 100 < runtimeConfig.shadowPercentage:
        candidatePayload = {**requestPayload, "model": settings.CANDIDATE_LLM_MODEL}
        submitted = await shadowPool.submit(_runShadow(candidatePayload, primaryResponse))
        if not submitted:
            await metricsStore.recordShed()

    return primaryResponse
