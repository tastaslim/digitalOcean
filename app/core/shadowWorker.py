import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from app.core.llmClient import _getContent, _parseAction
from app.core.shadowPool import ShadowPool
from app.domain.comparison import runComparison
from app.domain.models.enums import TaskType
from app.ports.blobStorage import BlobStoragePort
from app.ports.database import MismatchRepository
from app.ports.llm import LlmPort
from app.ports.messageQueue import Message, MessageQueuePort
from app.ports.shadowTask import ShadowTaskRepository
from app.resources.metrics.metricsService import MetricsService

logger = logging.getLogger(__name__)

SHADOW_TASKS_QUEUE = "shadow.tasks:{modelId}"


class ShadowWorker:
    """
    Background service that consumes shadow events from the queue, calls the
    candidate LLM, and coordinates comparison with the proxy side.

    Pipeline:
      Queue message contains only {taskId, taskType, messages} — no primary
      response. The candidate call starts as soon as the message is dequeued,
      which can overlap with the primary LLM call still running in the proxy.

      After the candidate call:
        1. Store result in blob storage.
        2. Upsert candidate result into shadow_tasks checkpoint row.
        3. Call tryClaimComparison() — atomic gate shared with the proxy side.
           If both primary and candidate are done, exactly one caller wins.
        4. Winner fetches the full task row and runs domain comparison.

    The candidate LLM provider is injected as a LlmPort — swap providers by
    changing CANDIDATE_LLM_* in cloud.env without touching this code.
    """

    def __init__(
        self,
        queue: MessageQueuePort,
        mismatchRepo: MismatchRepository,
        shadowTaskRepo: ShadowTaskRepository,
        storage: BlobStoragePort,
        metrics: MetricsService,
        candidateLlm: LlmPort,
        maxConcurrent: int = 50,
        contentMaxChars: int = 2000,
        queueName: str | None = None,
    ) -> None:
        self._queue = queue
        self._mismatchRepo = mismatchRepo
        self._shadowTaskRepo = shadowTaskRepo
        self._storage = storage
        self._metrics = metrics
        self.candidateLlm = candidateLlm  # public for test access via patch.object
        self._contentMaxChars = contentMaxChars
        self._queueNameOverride = queueName
        self.pool = ShadowPool(maxConcurrent=maxConcurrent)

    def queueName(self) -> str:
        # SQS needs a concrete queue URL (passed in via SQS_QUEUE_URL); the
        # in-memory adapter uses the logical per-model name and matches it
        # against the publish topic by prefix.
        if self._queueNameOverride:
            return self._queueNameOverride
        return SHADOW_TASKS_QUEUE.format(modelId=self.candidateLlm.modelId)

    async def start(self) -> None:
        logger.info(
            "ShadowWorker starting — model=%s queue=%s",
            self.candidateLlm.modelId,
            self.queueName(),
        )
        await self._queue.startConsumer(
            queueName=self.queueName(),
            handler=self._handle,
            maxConcurrent=self.pool._maxConcurrent,
        )

    async def _handle(self, message: Message) -> None:
        taskId = message.body.get("taskId", "unknown")
        submitted = await self.pool.submit(
            self._evaluate(message.body), name=f"shadow-eval-{taskId}"
        )
        if not submitted:
            await self._metrics.recordShed()

    async def _evaluate(self, body: dict) -> None:
        try:
            await self._runEvaluation(body)
        except Exception:
            logger.exception(
                "ShadowWorker: unhandled error for task %s", body.get("taskId")
            )
            await self._metrics.recordShadowResult(error=True)

    async def _runEvaluation(self, body: dict) -> None:
        taskType = TaskType(body["taskType"])
        messages = body["messages"]
        taskId = body["taskId"]

        # Candidate call — may overlap with the primary LLM still running in proxy.
        start = time.monotonic()
        try:
            candidateRaw = await asyncio.wait_for(
                self.candidateLlm.chat(messages),
                timeout=self.candidateLlm.timeoutSeconds,
            )
        except Exception as exc:
            logger.warning(
                "Candidate LLM %s failed for task %s: %s",
                self.candidateLlm.modelId,
                taskId,
                exc,
            )
            await self._metrics.recordShadowResult(error=True)
            return

        candidateLatencyMs = int((time.monotonic() - start) * 1000)
        candidateContent = _getContent(candidateRaw)[:self._contentMaxChars]
        candidateAction = _parseAction(candidateContent)

        # Store candidate response in blob storage.
        now = datetime.now(timezone.utc)
        candidateS3Path = (
            f"tasks/{taskType.value}/{now.strftime('%Y/%m/%d')}/{taskId}/candidate.json"
        )
        await self._storage.put(
            candidateS3Path,
            json.dumps({
                "model": self.candidateLlm.modelId,
                "decision": candidateAction.decision,
                "rawContent": candidateContent,
                "latencyMs": candidateLatencyMs,
                "timestamp": now.isoformat(),
            }).encode(),
        )

        # Upsert candidate result into the checkpoint row.
        await self._shadowTaskRepo.upsertCandidateDone(
            taskId=taskId,
            taskType=taskType,
            candidateS3Path=candidateS3Path,
            candidateModel=self.candidateLlm.modelId,
            candidateAction=candidateAction.decision,
            candidateContent=candidateContent,
            candidateLatencyMs=candidateLatencyMs,
        )

        # Atomic claim: returns True only if primary is already done and no
        # one else has claimed yet. This side wins the comparison race.
        claimed = await self._shadowTaskRepo.tryClaimComparison(taskId)
        if claimed:
            task = await self._shadowTaskRepo.getTask(taskId)
            if task:
                await runComparison(task, self._mismatchRepo, self._shadowTaskRepo, self._metrics)
