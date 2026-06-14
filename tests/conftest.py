import asyncio
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load cloud.env.example before any app module is imported.
# Sets QUEUE_BACKEND=memory, CACHE_BACKEND=memory, DB_BACKEND=sqlite,
# STORAGE_BACKEND=local so tests never touch external services.
# ---------------------------------------------------------------------------
_exampleEnv = Path(__file__).parent.parent / "cloud.env"
for _line in _exampleEnv.read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _key, _, _val = _line.partition("=")
        os.environ.setdefault(_key.strip(), _val.strip())

from app.db.settings import getSettings  # noqa: E402
from app.infrastructure.container import Container  # noqa: E402
from app.main import app  # noqa: E402
from app.resources.config.configService import ConfigService  # noqa: E402
from app.resources.metrics.metricsService import MetricsService  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixture payloads
# ---------------------------------------------------------------------------

PRIMARY_RESPONSE: dict = {
    "choices": [
        {
            "finish_reason": "stop",
            "index": 0,
            "message": {"content": '{"action": "buy"}', "role": "assistant"},
        }
    ],
    "model": "openai-gpt-oss-120b",
    "usage": {"total_tokens": 50},
}

CANDIDATE_RESPONSE_MATCH: dict = {
    "choices": [
        {
            "finish_reason": "stop",
            "index": 0,
            "message": {"content": '{"action": "buy"}', "role": "assistant"},
        }
    ],
    "model": "openai-gpt-oss-120b",
    "usage": {"total_tokens": 40},
}

CANDIDATE_RESPONSE_MISMATCH: dict = {
    "choices": [
        {
            "finish_reason": "stop",
            "index": 0,
            "message": {"content": '{"action": "sell"}', "role": "assistant"},
        }
    ],
    "model": "openai-gpt-oss-120b",
    "usage": {"total_tokens": 40},
}

CANDIDATE_RESPONSE_INVALID_JSON: dict = {
    "choices": [
        {
            "finish_reason": "stop",
            "index": 0,
            "message": {"content": "plain text, not JSON", "role": "assistant"},
        }
    ],
    "model": "openai-gpt-oss-120b",
    "usage": {},
}

CHAT_PAYLOAD: dict = {"messages": [{"role": "user", "content": "hello"}]}


# ---------------------------------------------------------------------------
# Session-scoped: build the Container and shadow worker once for the whole
# test run, then mount them on app.state so every test can access them.
# The main.py lifespan is guarded to skip init when the container is pre-set.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
async def _initTestContainer():
    settings = getSettings()
    container = Container(settings)
    await container.init()
    app.state.container = container

    from app.core.shadowWorker import ShadowWorker

    worker = ShadowWorker(
        queue=container.queue,
        mismatchRepo=container.mismatchRepository,
        metrics=MetricsService(cache=container.cache),
        candidateLlm=container.candidateLlm,
        maxConcurrent=settings.MAX_CONCURRENT_SHADOWS,
        contentMaxChars=settings.CONTENT_MAX_CHARS,
    )
    worker_task = asyncio.create_task(worker.start(), name="shadow-worker-test")
    await asyncio.sleep(0)  # Let the worker register its queue before tests start
    app.state.shadow_worker = worker

    yield

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await container.close()


# ---------------------------------------------------------------------------
# Function-scoped autouse: reset all in-memory state between tests.
# Depends on _initTestContainer so the container is guaranteed to exist.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def resetAdapters(_initTestContainer) -> None:
    await app.state.container.cache.reset()
    await app.state.container.mismatchRepository.reset()
    app.state.shadow_worker.pool._active = 0
    # Reset per-process caches so test isolation is guaranteed.
    ConfigService.clearCache()
    app.state.container.circuitBreaker.reset()
