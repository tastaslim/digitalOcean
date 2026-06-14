"""
Standalone shadow worker entrypoint — run as a separate process/container.

    python -m app.worker

In the distributed (QUEUE_BACKEND=sqs) deployment the proxy only publishes
shadow events to SNS; this process long-polls the SQS queue, calls the
candidate LLM, and coordinates comparison via the shared shadow_tasks table.
Scale the worker horizontally by running more replicas against the same queue.

When QUEUE_BACKEND=memory the proxy runs the worker in-process instead (see
app.main.lifespan), so this entrypoint is only meaningful for sqs.
"""

import asyncio
import logging
import signal

from app.common.logging.jsonFormatter import JsonFormatter
from app.core.shadowWorker import ShadowWorker
from app.db.settings import getSettings
from app.infrastructure.container import Container
from app.resources.metrics.metricsService import MetricsService

logger = logging.getLogger(__name__)


def _setupLogging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


async def main() -> None:
    _setupLogging()
    settings = getSettings()

    if settings.QUEUE_BACKEND == "sqs" and not settings.SQS_QUEUE_URL:
        raise RuntimeError(
            "SQS_QUEUE_URL must be set when running the standalone worker with "
            "QUEUE_BACKEND=sqs"
        )

    container = Container(settings)
    await container.init()

    worker = ShadowWorker(
        queue=container.queue,
        mismatchRepo=container.mismatchRepository,
        shadowTaskRepo=container.shadowTaskRepository,
        storage=container.storage,
        metrics=MetricsService(cache=container.cache),
        candidateLlm=container.candidateLlm,
        maxConcurrent=settings.MAX_CONCURRENT_SHADOWS,
        contentMaxChars=settings.CONTENT_MAX_CHARS,
        queueName=settings.SQS_QUEUE_URL or None,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    workerTask = asyncio.create_task(worker.start(), name="shadow-worker")
    logger.info("Shadow worker started — consuming %s", worker.queueName())

    await stop.wait()
    logger.info("Shutdown signal received — stopping shadow worker")

    workerTask.cancel()
    try:
        await workerTask
    except asyncio.CancelledError:
        pass
    await container.close()


if __name__ == "__main__":
    asyncio.run(main())
