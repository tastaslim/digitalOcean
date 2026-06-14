import asyncio
import os
import tempfile
from pathlib import Path

# mkstemp creates the file and returns an open fd + path. Close the fd
# immediately; SQLite (aiosqlite) opens its own connection to the path.
_tmpDbFd, _tmpDb = tempfile.mkstemp(suffix="_test_mismatches.db")
os.close(_tmpDbFd)

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
# Single shared event loop for the whole session.
#
# The container + shadow worker are session-scoped, but background DB tasks
# (archive-*, shadow-eval-*) are spawned inside whichever loop is running when
# a test publishes to the queue. With the default function-scoped loop, those
# tasks are abandoned when the test's loop closes — often mid-transaction,
# leaving the SQLite write lock held by a dead connection thread. The next
# test's drain runs in a *new* loop and cannot even see them, so it hits
# "database is locked". Pinning one loop for the session keeps every task in
# the same loop so resetAdapters can reliably drain them between tests.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    # NOTE: Overriding event_loop is deprecated in pytest-asyncio, but in
    # 0.23.x it is the only mechanism that makes the session-scoped container/
    # worker fixtures and the function-scoped tests share ONE loop. The marker
    # `scope="session"` alone does not pull the autouse session fixtures onto
    # the same loop, so background DB tasks still leak across loops. Revisit if
    # we upgrade to pytest-asyncio >= 0.24 (asyncio_default_test_loop_scope).
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
        shadowTaskRepo=container.shadowTaskRepository,
        storage=container.storage,
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
    # Drain background tasks from the previous test before touching shared
    # state. Without this, archive-* tasks may still hold a SQLite write lock
    # when reset() tries to DELETE, causing "database is locked".
    # Multi-pass drain: each pass may create new tasks (queue dispatch creates
    # shadow-eval, which creates comparison tasks). Repeat until stable.
    _BG_PREFIXES = ("archive-", "shadow-publish-", "metrics-", "shadow-eval-", "queue-dispatch-")
    for _ in range(6):
        pending = {
            t for t in asyncio.all_tasks()
            if not t.done()
            and t is not asyncio.current_task()
            and any(t.get_name().startswith(p) for p in _BG_PREFIXES)
        }
        if not pending:
            break
        await asyncio.wait(pending, timeout=2.0)

    await app.state.container.cache.reset()
    await app.state.container.mismatchRepository.reset()
    await app.state.container.shadowTaskRepository.reset()
    app.state.shadow_worker.pool._active = 0
    # Reset per-process caches so test isolation is guaranteed.
    ConfigService.clearCache()
    app.state.container.circuitBreaker.reset()
