import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Load cloud.env.example before any app module is imported so pydantic-settings
# has the required fields. Real credentials in cloud.env take precedence when
# present (os.environ.setdefault never overwrites an already-set variable).
# ---------------------------------------------------------------------------
_exampleEnv = Path(__file__).parent.parent / "cloud.env.example"
for _line in _exampleEnv.read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _key, _, _val = _line.partition("=")
        os.environ.setdefault(_key.strip(), _val.strip())

import pytest  # noqa: E402

from app.resources.config.configService import runtimeConfig  # noqa: E402
from app.resources.metrics.metricsService import metricsStore  # noqa: E402
from app.resources.proxy.proxyService import shadowPool  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixture data
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
# Autouse: reset all singletons between tests to prevent state bleed
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def resetSingletons() -> None:
    """Reset shared module-level singletons before every test."""
    metricsStore.totalRequests = 0
    metricsStore.shadowErrors = 0
    metricsStore.shadowCompleted = 0
    metricsStore.exactMatches = 0
    metricsStore.shedCount = 0

    runtimeConfig.shadowPercentage = 100.0

    shadowPool._active = 0
