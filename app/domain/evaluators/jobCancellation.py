from app.domain.evaluators.base import BaseEvaluator, EvaluationResult
from app.domain.models.enums import EvalStatus, Severity
from app.domain.models.task import TaskResponse


class JobCancellationEvaluator(BaseEvaluator):
    """
    Domain-aware evaluator for JOB_CANCELLATION tasks.

    Checks: decision, affectedResources overlap (>80%), rollbackPlan presence.
    CRITICAL when one model says cancel-immediate and the other says defer/reject —
    a live job would be left running or killed prematurely.
    """

    _CRITICAL_PAIRS = {
        frozenset({"cancel-immediate", "defer"}),
        frozenset({"cancel-immediate", "reject"}),
    }

    async def evaluate(self, primary: TaskResponse, candidate: TaskResponse) -> EvaluationResult:
        p = primary.action
        c = candidate.action
        diffFields: list[str] = []

        if not p.decision or not c.decision:
            return EvaluationResult(
                primary=primary,
                candidate=candidate,
                status=EvalStatus.PARSE_ERROR,
                severity=Severity.MEDIUM,
                notes="Missing decision field",
            )

        if p.decision != c.decision:
            diffFields.append("decision")

        pResources = set(p.domainPayload.get("affectedResources", []))
        cResources = set(c.domainPayload.get("affectedResources", []))
        if pResources and cResources:
            overlap = len(pResources & cResources) / max(len(pResources), len(cResources))
            if overlap < 0.8:
                diffFields.append("affectedResources")

        pHasRollback = bool(p.domainPayload.get("rollbackPlan"))
        cHasRollback = bool(c.domainPayload.get("rollbackPlan"))
        if pHasRollback != cHasRollback:
            diffFields.append("rollbackPlan")

        if not diffFields:
            return EvaluationResult(
                primary=primary,
                candidate=candidate,
                status=EvalStatus.EXACT_MATCH,
                severity=Severity.LOW,
                semanticScore=1.0,
            )

        pair = frozenset({p.decision, c.decision})
        severity = Severity.CRITICAL if pair in self._CRITICAL_PAIRS else Severity.HIGH

        return EvaluationResult(
            primary=primary,
            candidate=candidate,
            status=EvalStatus.MISMATCH,
            severity=severity,
            diffFields=diffFields,
            notes=f"primary={p.decision!r}, candidate={c.decision!r}",
        )
