from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from app.domain.models.enums import Severity, TaskType


@dataclass
class MismatchRecord:
    taskId: UUID
    taskType: TaskType
    primaryModel: str
    candidateModel: str
    primaryAction: str
    candidateAction: str
    primaryContent: str
    candidateContent: str
    severity: Severity
    diffFields: List[str]
    timestamp: datetime
    id: Optional[int] = None


@dataclass
class ModelConfig:
    modelId: str
    baseUrl: str
    apiKey: str
    isPrimary: bool = False
    isActive: bool = True
    weight: float = 1.0


class MismatchRepository(ABC):
    """Port for persisting and querying action-key mismatches."""

    @abstractmethod
    async def save(self, record: MismatchRecord) -> int:
        """Persist a mismatch record. Returns the auto-assigned integer ID."""

    @abstractmethod
    async def findByTaskType(
        self,
        taskType: TaskType,
        severity: Optional[Severity] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MismatchRecord]: ...

    async def reset(self) -> None:
        """Delete all records. Default no-op; override in concrete adapters for test isolation."""


class ModelFleetRepository(ABC):
    """Port for managing the registered set of LLM models (primary + candidates)."""

    @abstractmethod
    async def getActiveModels(self) -> List[ModelConfig]: ...

    @abstractmethod
    async def registerModel(self, config: ModelConfig) -> None:
        """Upsert a model registration."""

    @abstractmethod
    async def deactivateModel(self, modelId: str) -> None: ...
