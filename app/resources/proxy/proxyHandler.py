from typing import Any
import httpx
from fastapi import HTTPException
from app.resources.proxy.proxyService import proxyChat


async def handleChatRequest(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Delegate the chat payload to proxyChat and translate upstream HTTP errors
    into FastAPI HTTPExceptions so the global exception handler can format them.

    Args:
        payload: Validated, model-stripped OpenAI-compatible request body.

    Returns:
        The primary LLM's raw response dict.

    Raises:
        HTTPException 4xx/5xx: Forwarded from a non-2xx primary LLM response.
        HTTPException 502: Primary LLM was unreachable at the transport layer.
    """
    try:
        return await proxyChat(payload)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Primary LLM returned error: {exc.response.text}",
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Primary LLM unreachable: {exc}")
