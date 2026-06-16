"""Unit tests for ShadowWorker message validation — the gate that routes
poison messages to the dead-letter queue.

_validateMessage must RAISE on a structurally invalid message so the SQS
adapter skips the delete and SQS redrives it to the DLQ. A valid message must
pass silently.
"""

import pytest

from app.core.shadowWorker import ShadowWorker
from app.domain.models.enums import TaskType


def test_valid_message_passes() -> None:
    ShadowWorker._validateMessage(
        {
            "taskId": "abc-123",
            "taskType": TaskType.GENERIC.value,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )


def test_missing_task_id_raises() -> None:
    with pytest.raises(ValueError, match="missing taskId"):
        ShadowWorker._validateMessage(
            {"taskType": TaskType.GENERIC.value, "messages": [{"role": "user"}]}
        )


def test_missing_messages_raises() -> None:
    with pytest.raises(ValueError, match="missing 'messages'"):
        ShadowWorker._validateMessage(
            {"taskId": "abc-123", "taskType": TaskType.GENERIC.value}
        )


def test_empty_messages_raises() -> None:
    with pytest.raises(ValueError, match="missing 'messages'"):
        ShadowWorker._validateMessage(
            {"taskId": "abc-123", "taskType": TaskType.GENERIC.value, "messages": []}
        )


def test_unknown_task_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown taskType"):
        ShadowWorker._validateMessage(
            {
                "taskId": "abc-123",
                "taskType": "NOT_A_REAL_TYPE",
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
