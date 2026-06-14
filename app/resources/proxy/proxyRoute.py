import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.common.circuitBreaker import CircuitBreaker, CircuitOpenError
from app.infrastructure.dependencies import (
    getCircuitBreaker,
    getConfigService,
    getMetricsService,
    getPrimaryLlm,
    getQueue,
    getStorage,
)
from app.ports.blobStorage import BlobStoragePort
from app.ports.llm import LlmPort
from app.ports.messageQueue import MessageQueuePort
from app.resources.config.configService import ConfigService
from app.resources.metrics.metricsService import MetricsService
from app.resources.proxy.proxyDtos import ChatRequest
from app.resources.proxy.proxyService import ProxyService

proxyRoute = APIRouter(prefix="/v1", tags=["proxy"])


def _getProxyService(
    queue: MessageQueuePort = Depends(getQueue),
    storage: BlobStoragePort = Depends(getStorage),
    metrics: MetricsService = Depends(getMetricsService),
    config: ConfigService = Depends(getConfigService),
    primaryLlm: LlmPort = Depends(getPrimaryLlm),
    circuitBreaker: CircuitBreaker = Depends(getCircuitBreaker),
) -> ProxyService:
    return ProxyService(
        queue=queue,
        storage=storage,
        metrics=metrics,
        config=config,
        primaryLlm=primaryLlm,
        circuitBreaker=circuitBreaker,
    )


@proxyRoute.post("/chat")
async def chat(
    request: ChatRequest,
    proxySvc: ProxyService = Depends(_getProxyService),
) -> Any:
    """
    Proxy a chat completion request to the primary LLM and return its response.

    The same request is published to the shadow queue for async evaluation
    against the candidate LLM; candidate latency never affects this response.

    Error responses:
      400 — missing or invalid request body
      401 — missing or invalid X-API-Key (when auth is enabled)
      502 — primary LLM unreachable (transport error)
      503 — circuit breaker open; primary LLM is repeatedly failing
      504 — primary LLM exceeded PRIMARY_LLM_TIMEOUT_SECONDS
      5xx — primary LLM returned an error status
    """
    payload = request.model_dump(exclude_none=True, exclude={"model"})
    try:
        return await proxySvc.proxyChat(payload)
    except CircuitOpenError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (asyncio.TimeoutError, httpx.TimeoutException):
        # asyncio.TimeoutError  — outer wait_for deadline exceeded
        # httpx.TimeoutException — httpx read/connect/pool timeout (subclass of
        #   httpx.RequestError, so must be caught BEFORE the 502 handler below)
        raise HTTPException(
            status_code=504,
            detail="Primary LLM did not respond within the configured timeout.",
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Primary LLM returned error: {exc.response.text}",
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Primary LLM unreachable: {exc}")
