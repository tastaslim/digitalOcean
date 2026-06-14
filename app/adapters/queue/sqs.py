import asyncio
import json
import logging
from typing import Any, Dict, Optional

from app.ports.messageQueue import Message, MessageHandler, MessageQueuePort

logger = logging.getLogger(__name__)


class SQSAdapter(MessageQueuePort):
    """
    AWS SQS adapter with SNS fan-out on the publish side.

    Publish: sends to an SNS topic, which fans out to per-model SQS queues
    via infrastructure-managed subscriptions (Terraform/CDK). Adding a new
    candidate model requires only a new SNS subscription — no code change.

    Consume: long-polls a specific SQS queue URL, processes messages up to
    max_concurrent at a time, and deletes each message after successful handling.

    Requires: pip install boto3
    """

    def __init__(
        self,
        region: str,
        sns_topic_arn: str,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> None:
        try:
            import boto3
        except ImportError:
            raise RuntimeError("boto3 not installed — run: pip install boto3")

        session = boto3.Session(
            region_name=region,
            aws_access_key_id=aws_access_key_id or None,
            aws_secret_access_key=aws_secret_access_key or None,
        )
        kwargs = {"endpoint_url": endpoint_url} if endpoint_url else {}
        self._sns = session.client("sns", **kwargs)
        self._sqs = session.client("sqs", **kwargs)
        self._sns_topic_arn = sns_topic_arn

    async def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        attributes: Optional[Dict[str, str]] = None,
    ) -> str:
        msg_attrs = {
            k: {"DataType": "String", "StringValue": v}
            for k, v in (attributes or {}).items()
        }
        response = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._sns.publish(
                TopicArn=self._sns_topic_arn,
                Message=json.dumps(payload),
                MessageAttributes=msg_attrs,
            ),
        )
        return response["MessageId"]

    async def startConsumer(
        self,
        queueName: str,
        handler: MessageHandler,
        maxConcurrent: int = 10,
    ) -> None:
        """queueName is the full SQS queue URL."""
        sem = asyncio.Semaphore(maxConcurrent)

        while True:
            try:
                response = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._sqs.receive_message(
                        QueueUrl=queueName,
                        MaxNumberOfMessages=10,
                        WaitTimeSeconds=20,  # long-poll to reduce empty receives
                    ),
                )
                messages = response.get("Messages", [])
                tasks = [
                    asyncio.create_task(self._process(queueName, raw, handler, sem))
                    for raw in messages
                ]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("SQS receive_message failed; retrying in 5 s")
                await asyncio.sleep(5)

    async def _process(
        self,
        queue_url: str,
        raw: dict,
        handler: MessageHandler,
        sem: asyncio.Semaphore,
    ) -> None:
        async with sem:
            try:
                # SNS wraps the original payload in a JSON envelope.
                outer = json.loads(raw["Body"])
                body = json.loads(outer.get("Message", raw["Body"]))
                msg = Message(
                    id=raw["MessageId"],
                    body=body,
                    receiptHandle=raw["ReceiptHandle"],
                )
                await handler(msg)
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._sqs.delete_message(
                        QueueUrl=queue_url,
                        ReceiptHandle=raw["ReceiptHandle"],
                    ),
                )
            except Exception:
                logger.exception("Failed to process SQS message %s", raw.get("MessageId"))

    async def close(self) -> None:
        pass
