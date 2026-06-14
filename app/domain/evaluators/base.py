from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

from app.domain.models.enums import EvalStatus, Severity
from app.domain.models.task import TaskResponse


@dataclass
class EvaluationResult:
    primary: TaskResponse
    candidate: TaskResponse
    status: EvalStatus
    severity: Severity
    diffFields: List[str] = field(default_factory=list)
    semanticScore: float = 0.0
    notes: str = ""

    @property
    def isExactMatch(self) -> bool:
        return self.status == EvalStatus.EXACT_MATCH

    @property
    def isMismatch(self) -> bool:
        return self.status in (EvalStatus.MISMATCH, EvalStatus.SEMANTIC_MATCH)


class BaseEvaluator(ABC):
    @abstractmethod
    async def evaluate(
        self, primary: TaskResponse, candidate: TaskResponse
    ) -> EvaluationResult:
        """Compare primary and candidate responses. Implementations must never raise."""
