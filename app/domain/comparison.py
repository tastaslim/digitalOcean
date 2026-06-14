import logging
from datetime import datetime, timezone

from app.core.llmClient import _parseAction
from app.domain.evaluators.registry import EvaluatorRegistry
from app.domain.models.enums import EvalStatus, TaskType
from app.domain.models.task import TaskResponse
from app.ports.database import MismatchRecord, MismatchRepository
from app.ports.shadowTask import ShadowTask, ShadowTaskRepository
from app.resources.metrics.metricsService import MetricsService

logger = logging.getLogger(__name__)


async def runComparison(
    task: ShadowTask,
    mismatchRepo: MismatchRepository,
    shadowTaskRepo: ShadowTaskRepository,
    metrics: MetricsService,
) -> None:
    """
    Run domain evaluation for a completed shadow task and record all outcomes.

    Called by whichever side (proxy background task or worker) wins the atomic
    claim via shadowTaskRepo.tryClaimComparison(). The other side gets False
    and exits without running a duplicate comparison.
    """
    taskId = task.taskId
    taskType = task.taskType

    primaryResponse = TaskResponse(
        taskId=taskId,
        model=task.primaryModel or "",
        action=_parseAction(task.primaryContent or ""),
        latencyMs=task.primaryLatencyMs or 0,
        rawContent=task.primaryContent or "",
    )
    candidateResponse = TaskResponse(
        taskId=taskId,
        model=task.candidateModel or "",
        action=_parseAction(task.candidateContent or ""),
        latencyMs=task.candidateLatencyMs or 0,
        rawContent=task.candidateContent or "",
    )

    try:
        evaluator = EvaluatorRegistry.get(taskType)
        result = await evaluator.evaluate(primaryResponse, candidateResponse)

        if result.isMismatch and result.status != EvalStatus.PARSE_ERROR:
            record = MismatchRecord(
                taskId=taskId,
                taskType=taskType,
                primaryModel=primaryResponse.model,
                candidateModel=candidateResponse.model,
                primaryAction=primaryResponse.action.decision,
                candidateAction=candidateResponse.action.decision,
                primaryContent=primaryResponse.rawContent,
                candidateContent=candidateResponse.rawContent,
                severity=result.severity,
                diffFields=result.diffFields,
                timestamp=datetime.now(timezone.utc),
            )
            await mismatchRepo.save(record)

        outcome = "match" if result.isExactMatch else "mismatch"
        await shadowTaskRepo.markComparisonDone(str(taskId), outcome)
        await metrics.recordShadowResult(
            error=(result.status == EvalStatus.ERROR),
            exactMatch=result.isExactMatch,
        )
    except Exception:
        logger.exception("Comparison failed for task %s", taskId)
        await shadowTaskRepo.markComparisonDone(str(taskId), "error")
        await metrics.recordShadowResult(error=True)
