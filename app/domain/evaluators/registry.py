from typing import Dict, Type

from app.domain.evaluators.base import BaseEvaluator
from app.domain.evaluators.generic import GenericEvaluator
from app.domain.evaluators.jobCancellation import JobCancellationEvaluator
from app.domain.evaluators.orderCancellation import OrderCancellationEvaluator
from app.domain.models.enums import TaskType


class EvaluatorRegistry:
    """
    Maps TaskType to the domain-aware evaluator for that task.

    The default GENERIC evaluator is used as a fallback for any unregistered
    task type. Call :meth:`register` at startup to add custom evaluators without
    touching this file.
    """

    _registry: Dict[TaskType, BaseEvaluator] = {
        TaskType.JOB_CANCELLATION: JobCancellationEvaluator(),
        TaskType.ORDER_CANCELLATION: OrderCancellationEvaluator(),
        TaskType.GENERIC: GenericEvaluator(),
    }

    @classmethod
    def get(cls, taskType: TaskType) -> BaseEvaluator:
        return cls._registry.get(taskType, cls._registry[TaskType.GENERIC])

    @classmethod
    def register(cls, taskType: TaskType, evaluator: BaseEvaluator) -> None:
        cls._registry[taskType] = evaluator
