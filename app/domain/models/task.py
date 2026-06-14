from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID, uuid4

from app.domain.models.enums import TaskType


@dataclass
class ActionResult:
    decision: str
    confidence: float = 0.0
    reasoning: str = ""
    domainPayload: Dict[str, Any] = field(default_factory=dict)
    rawContent: str = ""


@dataclass
class BusinessContext:
    entityId: str = ""
    userId: str = ""
    tenantId: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskRequest:
    taskType: TaskType
    messages: List[Dict[str, str]]
    taskId: UUID = field(default_factory=uuid4)
    businessContext: BusinessContext = field(default_factory=BusinessContext)
    candidateModels: List[str] = field(default_factory=list)
    traceId: str = ""


@dataclass
class TaskResponse:
    taskId: UUID
    model: str
    action: ActionResult
    latencyMs: int
    rawContent: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ShadowEvent:
    taskId: UUID
    taskType: TaskType
    messages: List[Dict[str, str]]
    primaryResponse: TaskResponse
    candidateModels: List[str]
    businessContext: BusinessContext
    publishedAt: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    traceId: str = ""
