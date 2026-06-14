import asyncio
import os
import tempfile
from pathlib import Path

_tmpDb = tempfile.mktemp(suffix="_test_mismatches.db")

import pytest

# ---------------------------------------------------------------------------
# Bootstrap environment before any app module is imported.
#
# Priority (highest → lowest):
#   1. Variables already set in the process (CI secrets, docker-compose env)
#   2. cloud.env (local dev — gitignored, contains real keys)
#   3. Inline test-safe defaults below (CI fallback)
#
# All LLM calls are mocked in tests so API keys can be placeholders.
# ---------------------------------------------------------------------------
_cloudEnv = Path(__file__).parent.parent / "cloud.env"
if _cloudEnv.exists():
    for _line in _cloudEnv.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())

# Defaults that must always be set — safe for CI (no real services).
_TEST_DEFAULTS: dict[str, str] = {
    "QUEUE_BACKEND": "memory",
    "CACHE_BACKEND": "memory",
    "DB_BACKEND": "sqlite",
    "STORAGE_BACKEND": "local",
    "MISMATCH_DB_PATH": _tmpDb,
    "LOCAL_STORAGE_DIR": "/tmp/.shadow_storage_test",
    "PRIMARY_LLM_BASE_URL": "https://api.openai.com/v1",
    "PRIMARY_LLM_MODEL": "gpt-4o-mini",
    "PRIMARY_LLM_API_KEY": "test-key-primary",
    "CANDIDATE_LLM_BASE_URL": "https://api.groq.com/openai/v1",
    "CANDIDATE_LLM_MODEL": "llama-3.1-8b-instant",
    "CANDIDATE_LLM_API_KEY": "test-key-candidate",
    "SHADOW_TIMEOUT_SECONDS": "30",
    "MAX_CONCURRENT_SHADOWS": "50",
    "PRIMARY_LLM_TIMEOUT_SECONDS": "30",
    "CIRCUIT_BREAKER_FAILURE_THRESHOLD": "5",
    "CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS": "30",
    "CONTENT_MAX_CHARS": "2000",
    "PROXY_API_KEY": "",
}
for _k, _v in _TEST_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

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
