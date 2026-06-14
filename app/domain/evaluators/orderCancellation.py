from app.domain.evaluators.base import BaseEvaluator, EvaluationResult
from app.domain.models.enums import EvalStatus, Severity
from app.domain.models.task import TaskResponse


class OrderCancellationEvaluator(BaseEvaluator):
    """
    Domain-aware evaluator for ORDER_CANCELLATION tasks.

    Checks: decision, refundAmount (±$0.01 tolerance), reasonCode, restockFlag.
    CRITICAL when one model approves a full refund and the other rejects —
    the customer either gets charged or doesn't, with real financial exposure.
    MEDIUM when decisions match but refund amounts diverge by more than 5%.
    """

    _CRITICAL_PAIRS = {
        frozenset({"cancel-full-refund", "reject"}),
        frozenset({"cancel-full-refund", "cancel-no-refund"}),
    }
    _REFUND_EXACT_TOLERANCE = 0.01
    _REFUND_DIVERGENCE_THRESHOLD = 0.05

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
            )

        decisionMismatch = p.decision != c.decision
        if decisionMismatch:
            diffFields.append("decision")

        refundSeverity = Severity.LOW
        pRefund = p.domainPayload.get("refundAmount")
        cRefund = c.domainPayload.get("refundAmount")
        if pRefund is not None and cRefund is not None:
            try:
                pAmt, cAmt = float(pRefund), float(cRefund)
                if abs(pAmt - cAmt) > self._REFUND_EXACT_TOLERANCE:
                    diffFields.append("refundAmount")
                    avg = (pAmt + cAmt) / 2
                    if avg > 0 and abs(pAmt - cAmt) / avg > self._REFUND_DIVERGENCE_THRESHOLD:
                        refundSeverity = Severity.MEDIUM
            except (TypeError, ValueError):
                pass

        if p.domainPayload.get("reasonCode") != c.domainPayload.get("reasonCode"):
            diffFields.append("reasonCode")
        if p.domainPayload.get("restockFlag") != c.domainPayload.get("restockFlag"):
            diffFields.append("restockFlag")

        if not diffFields:
            return EvaluationResult(
                primary=primary,
                candidate=candidate,
                status=EvalStatus.EXACT_MATCH,
                severity=Severity.LOW,
                semanticScore=1.0,
            )

        if decisionMismatch:
            pair = frozenset({p.decision, c.decision})
            severity = Severity.CRITICAL if pair in self._CRITICAL_PAIRS else Severity.HIGH
        else:
            severity = refundSeverity

        return EvaluationResult(
            primary=primary,
            candidate=candidate,
            status=EvalStatus.MISMATCH,
            severity=severity,
            diffFields=diffFields,
        )
