import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.common.exceptionHandlers import registerExceptionHandlers
from app.common.logging.jsonFormatter import JsonFormatter
from app.common.middleware.authMiddleware import ApiKeyMiddleware
from app.common.middleware.requestIdMiddleware import RequestIdMiddleware
from app.db.settings import getSettings
from app.infrastructure.container import Container
from app.resources.metrics.metricsService import MetricsService
from app.routes.allRoutes import routers


def _setupLogging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Build the DI container, run adapter init (schema creation), then start the
    inline shadow worker when QUEUE_BACKEND=memory.

    For SQS: omit the inline worker and deploy ShadowWorker as a separate
    service pointing at the same queue infrastructure.
    """
    ownContainer = not hasattr(_app.state, "container")
    if ownContainer:
        settings = getSettings()
        container = Container(settings)
        await container.init()
        _app.state.container = container
    else:
        settings = getSettings()

    workerTask: asyncio.Task | None = None

    if ownContainer and settings.QUEUE_BACKEND == "memory":
        from app.core.shadowWorker import ShadowWorker

        worker = ShadowWorker(
            queue=container.queue,
            mismatchRepo=container.mismatchRepository,
            metrics=MetricsService(cache=container.cache),
            candidateLlm=container.candidateLlm,
            maxConcurrent=settings.MAX_CONCURRENT_SHADOWS,
            contentMaxChars=settings.CONTENT_MAX_CHARS,
        )
        workerTask = asyncio.create_task(worker.start(), name="shadow-worker")
        await asyncio.sleep(0)
        _app.state.shadow_worker = worker

    yield

    if workerTask is not None:
        workerTask.cancel()
        try:
            await workerTask
        except asyncio.CancelledError:
            pass

    if ownContainer:
        await _app.state.container.close()


def createApp() -> FastAPI:
    _setupLogging()
    settings = getSettings()

    app = FastAPI(title="LLM Shadow Proxy", lifespan=lifespan)

    # Middleware executes in reverse registration order (last added = outermost).
    # We want: RequestId (outermost, runs first) → ApiKey → route handler
    app.add_middleware(ApiKeyMiddleware, apiKey=settings.PROXY_API_KEY)
    app.add_middleware(RequestIdMiddleware)

    registerExceptionHandlers(app)
    for router in routers:
        app.include_router(router=router)
    return app


app = createApp()
