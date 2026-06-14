import json
from typing import Any

import httpx

from app.domain.models.task import ActionResult


async def _callLlm(
    client: httpx.AsyncClient,
    url: str,
    apiKey: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    POST a chat completion payload to an LLM endpoint and return the parsed JSON.

    Module-level function so both ProxyService and ShadowWorker share one
    implementation, and tests can patch a single target:
        mock.patch("app.core.llmClient._callLlm", ...)

    :raises httpx.HTTPStatusError: Non-2xx response from the endpoint.
    :raises httpx.RequestError: Transport-level failure (DNS, connection refused, etc.).
    """
    response = await client.post(
        url,
        headers={"Authorization": f"Bearer {apiKey}", "Content-Type": "application/json"},
        json=payload,
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()


def _getContent(llmResponse: dict[str, Any]) -> str:
    try:
        return llmResponse["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _extractAction(llmResponse: dict) -> str | None:
    """Extract the `action` key from an LLM response dict. Returns None on any parse failure."""
    content = _getContent(llmResponse)
    if not content:
        return None
    action = _parseAction(content)
    return action.decision or None


def _parseAction(content: str) -> ActionResult:
    try:
        parsed = json.loads(content)
        return ActionResult(
            decision=str(parsed.get("action", "")),
            confidence=float(parsed.get("confidence", 0.0)),
            reasoning=str(parsed.get("reasoning", "")),
            domainPayload={
                k: v
                for k, v in parsed.items()
                if k not in {"action", "confidence", "reasoning"}
            },
            rawContent=content,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return ActionResult(decision="", rawContent=content)
