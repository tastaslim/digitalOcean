from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from app.domain.models.enums import TaskType


@dataclass
class ShadowTask:
    taskId: UUID
    taskType: TaskType
    createdAt: datetime

    primaryS3Path: Optional[str] = None
    primaryModel: Optional[str] = None
    primaryAction: Optional[str] = None
    primaryContent: Optional[str] = None
    primaryLatencyMs: Optional[int] = None
    isPrimaryDone: bool = False
    primaryCompletedAt: Optional[datetime] = None

    candidateS3Path: Optional[str] = None
    candidateModel: Optional[str] = None
    candidateAction: Optional[str] = None
    candidateContent: Optional[str] = None
    candidateLatencyMs: Optional[int] = None
    isCandidateDone: bool = False
    candidateCompletedAt: Optional[datetime] = None

    isComparisonTriggered: bool = False
    isComparisonDone: bool = False
    comparisonResult: Optional[str] = None
    comparisonCompletedAt: Optional[datetime] = None


class ShadowTaskRepository(ABC):
    """
    Port for the shadow_tasks checkpoint table.

    Each row tracks the full pipeline state for one shadow evaluation:
      candidate call (parallel with primary) → both mark done → comparison.

    tryClaimComparison is the atomic gate: exactly one caller — the proxy
    background task or the worker — wins when both sides have written their
    result. The loser gets False and exits without running a duplicate
    comparison.
    """

    @abstractmethod
    async def upsertPrimaryDone(
        self,
        taskId: str,
        taskType: TaskType,
        primaryS3Path: str,
        primaryModel: str,
        primaryAction: str,
        primaryContent: str,
        primaryLatencyMs: int,
    ) -> None:
        """Create or update the task row with the primary LLM result."""

    @abstractmethod
    async def upsertCandidateDone(
        self,
        taskId: str,
        taskType: TaskType,
        candidateS3Path: str,
        candidateModel: str,
        candidateAction: str,
        candidateContent: str,
        candidateLatencyMs: int,
    ) -> None:
        """Create or update the task row with the candidate LLM result."""

    @abstractmethod
    async def tryClaimComparison(self, taskId: str) -> bool:
        """
        Atomically claim the comparison for this task.

        Returns True only when:
          - both isPrimaryDone and isCandidateDone are true, AND
          - isComparisonTriggered was false (this is the first claim).

        Exactly one concurrent caller returns True.
        """

    @abstractmethod
    async def getTask(self, taskId: str) -> Optional[ShadowTask]:
        """Fetch the full task row so the winner can read both results."""

    @abstractmethod
    async def markComparisonDone(self, taskId: str, comparisonResult: str) -> None:
        """Record the final comparison outcome: 'match' | 'mismatch' | 'error'."""

    @abstractmethod
    async def findStalled(self, olderThanSeconds: int = 3600) -> List[ShadowTask]:
        """
        Find tasks where comparison was never completed.
        Used by periodic recovery jobs to re-trigger stuck evaluations.
        """

    async def reset(self) -> None:
        """Delete all records. Override in concrete adapters for test isolation."""
