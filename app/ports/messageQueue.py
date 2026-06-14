from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional


@dataclass
class Message:
    id: str
    body: Dict[str, Any]
    attributes: Dict[str, str] = field(default_factory=dict)
    receiptHandle: str = ""


MessageHandler = Callable[[Message], Awaitable[None]]


class MessageQueuePort(ABC):
    """
    Port for durable, async message passing.

    Implementations: InMemoryQueueAdapter (dev/test), SQSAdapter.

    Fan-out to N workers (one per candidate model) is handled at the infrastructure
    layer — SNS subscriptions for SQS, keyed publish for in-memory — so application
    code always calls publish() to a single logical topic.
    """

    @abstractmethod
    async def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        attributes: Optional[Dict[str, str]] = None,
    ) -> str:
        """Publish payload to topic. Returns a message ID."""

    @abstractmethod
    async def startConsumer(
        self,
        queueName: str,
        handler: MessageHandler,
        maxConcurrent: int = 10,
    ) -> None:
        """
        Consume from queueName, calling handler for each message.
        Runs until the task is cancelled. Handles retries and ack/delete internally.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release connections and resources."""
