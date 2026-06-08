import pytest

from app.resources.config.configService import runtimeConfig
from app.resources.metrics.metricsService import metricsStore
from app.resources.proxy.proxyService import shadowPool

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
