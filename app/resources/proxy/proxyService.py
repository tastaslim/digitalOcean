import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.common.circuitBreaker import CircuitBreaker
from app.common.taskUtils import safeTask
from app.core.llmClient import _getContent, _parseAction
from app.domain.models.enums import TaskType
from app.ports.blobStorage import BlobStoragePort
from app.ports.llm import LlmPort
from app.ports.messageQueue import MessageQueuePort
from app.resources.config.configService import ConfigService
from app.resources.metrics.metricsService import MetricsService

logger = logging.getLogger(__name__)

SHADOW_TASKS_TOPIC = "shadow.tasks"


class ProxyService:
    """
    Critical-path contract: the ONLY blocking operation is the primary LLM call
    (wrapped with a timeout and a circuit breaker). Everything else — SQS publish,
    S3 archive, metrics increment — is a fire-and-forget background task that
    never touches response latency.

    The primary LLM provider is injected as a LlmPort. Swap providers (OpenAI →
    Groq → DigitalOcean inference → …) by changing cloud.env — no code changes.
    """

    def __init__(
        self,
        queue: MessageQueuePort,
        storage: BlobStoragePort,
        metrics: MetricsService,
        config: ConfigService,
        primaryLlm: LlmPort,
        circuitBreaker: CircuitBreaker,
    ) -> None:
        self._queue = queue
        self._storage = storage
        self._metrics = metrics
        self._config = config
        self._primaryLlm = primaryLlm
        self._breaker = circuitBreaker

    async def proxyChat(self, requestPayload: dict[str, Any]) -> dict[str, Any]:
        # Shadow sampling — single Redis GET (~0.5 ms), TTL-cached in process.
        shadowPct = await self._config.getShadowPercentage()
        doShadow = random.random() * 100 < shadowPct

        # One taskId ties the shadow event and the S3 archive together.
        taskId = str(uuid4())

        messages = requestPayload.get("messages", [])

        start = time.monotonic()
        # Circuit breaker wraps the LLM call; raises CircuitOpenError when OPEN.
        # wait_for provides a hard outer deadline matching the adapter's timeout.
        primaryResponse = await asyncio.wait_for(
            self._breaker.call(self._primaryLlm.chat(messages)),
            timeout=self._primaryLlm.timeoutSeconds,
        )
        latencyMs = int((time.monotonic() - start) * 1000)

        # ── Return to client here. All tasks below are fire-and-forget. ──────
        safeTask(self._metrics.incrementRequests(), name="metrics-increment")

        if doShadow:
            safeTask(
                self._publishShadow(taskId, messages, primaryResponse, latencyMs),
                name=f"shadow-publish-{taskId}",
            )

        safeTask(
            self._archive(taskId, messages, primaryResponse, latencyMs),
            name=f"archive-{taskId}",
        )

        return primaryResponse

    async def _publishShadow(
        self,
        taskId: str,
        messages: list[dict[str, Any]],
        primaryResponse: dict[str, Any],
        latencyMs: int,
    ) -> None:
        primaryContent = _getContent(primaryResponse)
        primaryAction = _parseAction(primaryContent)

        payload = {
            "taskId": taskId,
            "taskType": TaskType.GENERIC.value,
            "messages": messages,
            "primaryResponse": {
                "model": self._primaryLlm.modelId,
                "decision": primaryAction.decision,
                "rawContent": primaryContent,
                "latencyMs": latencyMs,
            },
            "publishedAt": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self._queue.publish(SHADOW_TASKS_TOPIC, payload)
        except Exception:
            logger.warning("Failed to publish shadow event for task %s", taskId)

    async def _archive(
        self,
        taskId: str,
        messages: list[dict[str, Any]],
        primaryResponse: dict[str, Any],
        latencyMs: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        path = f"tasks/GENERIC/{now.strftime('%Y/%m/%d')}/{taskId}.json"
        data = json.dumps(
            {
                "taskId": taskId,
                "taskType": "GENERIC",
                "messages": messages,
                "primaryContent": _getContent(primaryResponse),
                "latencyMs": latencyMs,
                "timestamp": now.isoformat(),
            }
        ).encode()
        try:
            await self._storage.put(path, data)
        except Exception:
            logger.warning("Failed to archive task %s to blob storage", taskId)
