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
from app.common.telemetry import injectContext
from app.core.llmClient import _getContent, _parseAction
from app.domain.comparison import runComparison
from app.domain.models.enums import TaskType
from app.ports.blobStorage import BlobStoragePort
from app.ports.database import MismatchRepository
from app.ports.llm import LlmPort
from app.ports.messageQueue import MessageQueuePort
from app.ports.shadowTask import ShadowTaskRepository
from app.resources.config.configService import ConfigService
from app.resources.metrics.metricsService import MetricsService

logger = logging.getLogger(__name__)

SHADOW_TASKS_TOPIC = "shadow.tasks"

# Hard deadlines for fire-and-forget background tasks.
# Prevents stalled S3/SQS from accumulating tasks in the event loop indefinitely.
_PUBLISH_TIMEOUT_S = 10.0
_ARCHIVE_TIMEOUT_S = 30.0


class ProxyService:
    """
    Critical-path contract: the ONLY blocking operation is the primary LLM call
    (wrapped with a timeout and a circuit breaker). Everything else is background.

    Shadow pipeline (true parallelism):
      1. Fire shadow queue message BEFORE awaiting the primary LLM response so
         the worker can start the candidate call while primary is still running.
      2. After primary responds, a background task writes the result to S3 and
         the shadow_tasks checkpoint table, then tries to atomically claim the
         comparison.
      3. The worker does the same for the candidate side.
      4. Whichever side finishes last wins the atomic claim and runs comparison
         exactly once — no duplicate evaluations, even under concurrent load.

    Checkpointing via shadow_tasks means a worker crash mid-evaluation can be
    recovered by a periodic job that re-queues stalled rows.
    """

    def __init__(
        self,
        queue: MessageQueuePort,
        storage: BlobStoragePort,
        metrics: MetricsService,
        config: ConfigService,
        primaryLlm: LlmPort,
        circuitBreaker: CircuitBreaker,
        shadowTaskRepo: ShadowTaskRepository,
        mismatchRepo: MismatchRepository,
        contentMaxChars: int = 2000,
    ) -> None:
        self._queue = queue
        self._storage = storage
        self._metrics = metrics
        self._config = config
        self._primaryLlm = primaryLlm
        self._breaker = circuitBreaker
        self._shadowTaskRepo = shadowTaskRepo
        self._mismatchRepo = mismatchRepo
        self._contentMaxChars = contentMaxChars

    async def proxyChat(self, requestPayload: dict[str, Any]) -> dict[str, Any]:
        # Shadow sampling — single Redis GET (~0.5 ms), TTL-cached in process.
        shadowPct = await self._config.getShadowPercentage()
        doShadow = random.random() * 100 < shadowPct

        taskId = str(uuid4())
        messages = requestPayload.get("messages", [])
        # Forward extra params (temperature, max_tokens, top_p, …) verbatim.
        extra = {k: v for k, v in requestPayload.items() if k != "messages"}

        # Publish to queue BEFORE awaiting the primary call so the worker can
        # start the candidate LLM call while we are still waiting for primary.
        if doShadow:
            # Inject the active trace context into the message attributes so the
            # worker's spans link into this request's trace across the SNS->SQS hop.
            traceCarrier = injectContext({})
            safeTask(
                self._queue.publish(
                    SHADOW_TASKS_TOPIC,
                    {"taskId": taskId, "taskType": TaskType.GENERIC.value, "messages": messages},
                    attributes=traceCarrier,
                ),
                name=f"shadow-publish-{taskId}",
                timeoutSeconds=_PUBLISH_TIMEOUT_S,
                onError=lambda _: self._metrics.recordBackgroundError(),
            )

        start = time.monotonic()
        # Circuit breaker wraps the LLM call; raises CircuitOpenError when OPEN.
        # wait_for provides a hard outer deadline matching the adapter's timeout.
        primaryResponse = await asyncio.wait_for(
            self._breaker.call(self._primaryLlm.chat(messages, **extra)),
            timeout=self._primaryLlm.timeoutSeconds,
        )
        latencyMs = int((time.monotonic() - start) * 1000)

        # ── Return to client here. All tasks below are fire-and-forget. ──────
        safeTask(self._metrics.incrementRequests(), name="metrics-increment")

        safeTask(
            self._archivePrimaryAndMaybeCompare(taskId, messages, primaryResponse, latencyMs, doShadow),
            name=f"archive-{taskId}",
            timeoutSeconds=_ARCHIVE_TIMEOUT_S,
            onError=lambda _: self._metrics.recordBackgroundError(),
        )

        return primaryResponse

    async def _archivePrimaryAndMaybeCompare(
        self,
        taskId: str,
        messages: list[dict[str, Any]],
        primaryResponse: dict[str, Any],
        latencyMs: int,
        doShadow: bool,
    ) -> None:
        now = datetime.now(timezone.utc)
        primaryContent = _getContent(primaryResponse)[:self._contentMaxChars]
        primaryAction = _parseAction(primaryContent)

        # Archive full primary response to blob storage (always, regardless of shadow).
        primaryS3Path = f"tasks/GENERIC/{now.strftime('%Y/%m/%d')}/{taskId}/primary.json"
        await self._storage.put(
            primaryS3Path,
            json.dumps({
                "model": self._primaryLlm.modelId,
                "decision": primaryAction.decision,
                "rawContent": primaryContent,
                "latencyMs": latencyMs,
                "messages": messages,
                "timestamp": now.isoformat(),
            }).encode(),
        )

        if not doShadow:
            return

        # Write primary result into the shadow_tasks checkpoint row.
        await self._shadowTaskRepo.upsertPrimaryDone(
            taskId=taskId,
            taskType=TaskType.GENERIC,
            primaryS3Path=primaryS3Path,
            primaryModel=self._primaryLlm.modelId,
            primaryAction=primaryAction.decision,
            primaryContent=primaryContent,
            primaryLatencyMs=latencyMs,
        )

        # Atomic claim: returns True only if candidate is already done and no
        # one else has claimed yet. This side wins the comparison race.
        claimed = await self._shadowTaskRepo.tryClaimComparison(taskId)
        if claimed:
            task = await self._shadowTaskRepo.getTask(taskId)
            if task:
                await runComparison(task, self._mismatchRepo, self._shadowTaskRepo, self._metrics)
