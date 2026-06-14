import asyncio
import uuid
from typing import Any

from app.ports.messageQueue import Message, MessageHandler, MessageQueuePort


class InMemoryQueueAdapter(MessageQueuePort):
    """
    Single-process in-memory queue for local dev and tests.

    Fan-out: any queue whose name starts with "{topic}:" receives messages
    published to that topic — mirroring the SNS-to-SQS subscription model.

    Design note: handlers are stored in a dict and tasks are created inside
    publish() on the *calling* event loop. This avoids asyncio.Queue's loop
    affinity, so a session-scoped worker can register once and tests running
    in their own event loop can still trigger processing via asyncio.sleep().

    Not for production — state is lost on restart and there is no durability.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, tuple[MessageHandler, asyncio.Semaphore]] = {}

    async def publish(
        self,
        topic: str,
        payload: dict[str, Any],
        attributes: dict[str, str] | None = None,
    ) -> str:
        msgId = str(uuid.uuid4())
        msg = Message(id=msgId, body=payload, attributes=attributes or {})
        for queueName, (handler, sem) in self._handlers.items():
            if queueName.startswith(f"{topic}:") or queueName == topic:
                async def _dispatch(h: MessageHandler = handler, m: Message = msg, s: asyncio.Semaphore = sem) -> None:
                    async with s:
                        await h(m)

                asyncio.create_task(_dispatch(), name=f"queue-dispatch-{msgId[:8]}")
        return msgId

    async def startConsumer(
        self,
        queueName: str,
        handler: MessageHandler,
        maxConcurrent: int = 10,
    ) -> None:
        self._handlers[queueName] = (handler, asyncio.Semaphore(maxConcurrent))
        # Park here until cancelled; actual dispatch happens inside publish().
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        self._handlers.clear()
