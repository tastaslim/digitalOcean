import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from app.core.llmClient import _getContent, _parseAction
from app.core.shadowPool import ShadowPool
from app.domain.evaluators.registry import EvaluatorRegistry
from app.domain.models.enums import EvalStatus, TaskType
from app.domain.models.task import ActionResult, TaskResponse
from app.ports.database import MismatchRecord, MismatchRepository
from app.ports.llm import LlmPort
from app.ports.messageQueue import Message, MessageQueuePort
from app.resources.metrics.metricsService import MetricsService

logger = logging.getLogger(__name__)

SHADOW_TASKS_QUEUE = "shadow.tasks:{modelId}"


class ShadowWorker:
    """
    Separate background service that consumes shadow events from the queue,
    calls the candidate LLM, runs domain-aware evaluation, and records
    mismatches and metrics.

    The proxy (control plane) never calls the candidate — it only publishes
    messages + primaryResponse to the queue and returns immediately to the
    client. This service handles everything after that, independently and
    at its own scale.

    The candidate LLM provider is injected as a LlmPort. Swap providers by
    changing CANDIDATE_LLM_* in cloud.env without touching this code.
    """

    def __init__(
        self,
        queue: MessageQueuePort,
        mismatchRepo: MismatchRepository,
        metrics: MetricsService,
        candidateLlm: LlmPort,
        maxConcurrent: int = 50,
        contentMaxChars: int = 2000,
    ) -> None:
        self._queue = queue
        self._mismatchRepo = mismatchRepo
        self._metrics = metrics
        self.candidateLlm = candidateLlm  # public for test access via patch.object
        self._contentMaxChars = contentMaxChars
        self.pool = ShadowPool(maxConcurrent=maxConcurrent)

    def queueName(self) -> str:
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
        submitted = await self.pool.submit(self._evaluate(message.body))
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
        primaryRaw = body["primaryResponse"]

        # Full LLM content can be several KB. Truncate before storing in the DB
        # row — the complete text already lives in the S3 archive keyed by taskId.
        primaryRawContent = (primaryRaw.get("rawContent", ""))[:self._contentMaxChars]

        primaryResponse = TaskResponse(
            taskId=UUID(body["taskId"]) if isinstance(body["taskId"], str) else body["taskId"],
            model=primaryRaw["model"],
            action=ActionResult(
                decision=primaryRaw.get("decision", ""),
                rawContent=primaryRawContent,
            ),
            latencyMs=primaryRaw.get("latencyMs", 0),
            rawContent=primaryRawContent,
        )

        try:
            candidateRaw = await asyncio.wait_for(
                self.candidateLlm.chat(messages),
                timeout=self.candidateLlm.timeoutSeconds,
            )
        except Exception as exc:
            logger.warning(
                "Candidate LLM %s failed for task %s: %s",
                self.candidateLlm.modelId,
                body.get("taskId"),
                exc,
            )
            await self._metrics.recordShadowResult(error=True)
            return

        candidateContent = _getContent(candidateRaw)[:self._contentMaxChars]
        candidateResponse = TaskResponse(
            taskId=primaryResponse.taskId,
            model=self.candidateLlm.modelId,
            action=_parseAction(candidateContent),
            latencyMs=0,
            rawContent=candidateContent,
        )

        evaluator = EvaluatorRegistry.get(taskType)
        result = await evaluator.evaluate(primaryResponse, candidateResponse)

        if result.isMismatch and result.status != EvalStatus.PARSE_ERROR:
            record = MismatchRecord(
                taskId=primaryResponse.taskId,
                taskType=taskType,
                primaryModel=primaryResponse.model,
                candidateModel=self.candidateLlm.modelId,
                primaryAction=primaryResponse.action.decision,
                candidateAction=candidateResponse.action.decision,
                primaryContent=primaryResponse.rawContent,
                candidateContent=candidateContent,
                severity=result.severity,
                diffFields=result.diffFields,
                timestamp=datetime.now(timezone.utc),
            )
            await self._mismatchRepo.save(record)

        await self._metrics.recordShadowResult(
            error=(result.status == EvalStatus.ERROR),
            exactMatch=result.isExactMatch,
        )
