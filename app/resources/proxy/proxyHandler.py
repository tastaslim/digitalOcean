from typing import Any
import httpx
from fastapi import HTTPException
from app.resources.proxy.proxyService import proxyChat


async def handleChatRequest(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Delegate the chat payload to :func:`proxyChat` and translate upstream HTTP
    errors into FastAPI ``HTTPException`` so the global handler can format them.

    :param payload: Validated, model-stripped OpenAI-compatible request body.
    :type payload: dict[str, Any]
    :return: The primary LLM's raw response dict.
    :rtype: dict[str, Any]
    :raises HTTPException: 4xx/5xx forwarded from a non-2xx primary LLM response,
        or 502 if the primary LLM was unreachable at the transport layer.
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
