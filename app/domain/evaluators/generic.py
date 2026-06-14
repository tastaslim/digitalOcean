from app.domain.evaluators.base import BaseEvaluator, EvaluationResult
from app.domain.models.enums import EvalStatus, Severity
from app.domain.models.task import TaskResponse


class GenericEvaluator(BaseEvaluator):
    """Fallback evaluator: exact string equality on the `decision` field."""

    async def evaluate(self, primary: TaskResponse, candidate: TaskResponse) -> EvaluationResult:
        p = primary.action.decision
        c = candidate.action.decision

        if not p or not c:
            return EvaluationResult(
                primary=primary,
                candidate=candidate,
                status=EvalStatus.PARSE_ERROR,
                severity=Severity.MEDIUM,
                notes="One or both responses missing decision",
            )

        if p == c:
            return EvaluationResult(
                primary=primary,
                candidate=candidate,
                status=EvalStatus.EXACT_MATCH,
                severity=Severity.LOW,
                semanticScore=1.0,
            )

        return EvaluationResult(
            primary=primary,
            candidate=candidate,
            status=EvalStatus.MISMATCH,
            severity=Severity.HIGH,
            diffFields=["decision"],
            notes=f"primary={p!r}, candidate={c!r}",
        )
