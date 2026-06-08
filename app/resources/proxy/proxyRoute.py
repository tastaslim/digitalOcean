from typing import Any
from fastapi import APIRouter
from app.resources.proxy.proxyDtos import ChatRequest
from app.resources.proxy.proxyHandler import handleChatRequest

proxyRoute = APIRouter(prefix="/v1", tags=["proxy"])


@proxyRoute.post("/chat")
async def chat(request: ChatRequest) -> Any:
    """
    Proxy a chat completion request to the primary LLM and return its response.
    The same request is concurrently dispatched to the candidate LLM in the background.

    Args:
        request: Validated OpenAI-compatible chat request body.

    Returns:
        The primary LLM's raw chat completion response.
    """
    # model is excluded here; the service injects PRIMARY/CANDIDATE model names per call.
    payload = request.model_dump(exclude_none=True, exclude={"model"})
    return await handleChatRequest(payload)
