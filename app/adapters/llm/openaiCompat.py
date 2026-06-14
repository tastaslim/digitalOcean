import asyncio
import logging
from typing import Any

import httpx

from app.ports.llm import LlmPort

logger = logging.getLogger(__name__)

# Statuses that are safe to retry — transient server-side issues.
_RETRY_STATUSES: frozenset[int] = frozenset({429, 503})


class OpenAICompatAdapter(LlmPort):
    """
    Chat completion adapter for any OpenAI-compatible REST endpoint.

    Supported providers (all share the /chat/completions + Bearer auth shape):
      - DigitalOcean inference  (https://inference.do-ai.run/v1)
      - OpenAI                  (https://api.openai.com/v1)
      - Groq                    (https://api.groq.com/openai/v1)
      - Together AI, Mistral, Anyscale, …

    Connection pooling:
      A single httpx.AsyncClient is created at construction and reused across
      all calls. This keeps TCP connections alive between requests, avoiding the
      TCP + TLS handshake overhead (~8-15 ms) on every call. Call close() when
      the adapter is no longer needed (wired into Container.close()).

    Retry policy:
      Transient 429 / 503 responses are retried up to maxRetries times with
      exponential backoff (1 s, 2 s, …). Retries happen *inside* the adapter
      so the circuit breaker only counts the final failure, not each attempt.
    """

    def __init__(
        self,
        baseUrl: str,
        apiKey: str,
        model: str,
        timeoutSeconds: int = 30,
        maxRetries: int = 2,
    ) -> None:
        self._baseUrl = baseUrl.rstrip("/")
        self._apiKey = apiKey
        self._model = model
        self._timeoutSeconds = timeoutSeconds
        self._maxRetries = maxRetries
        # Persistent client — shared across all requests, provides connection pooling.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=float(timeoutSeconds),
                write=10.0,
                pool=5.0,
            ),
            headers={
                "Authorization": f"Bearer {self._apiKey}",
                "Content-Type": "application/json",
            },
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30,
            ),
        )

    @property
    def modelId(self) -> str:
        return self._model

    @property
    def timeoutSeconds(self) -> int:
        return self._timeoutSeconds

    async def chat(self, messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        """
        POST to /chat/completions; retries on 429/503 with exponential backoff.

        Extra keyword arguments are merged into the JSON body so callers can
        pass temperature, max_tokens, top_p, etc. without any adapter changes.
        """
        payload = {"model": self._model, "messages": messages, **extra}
        url = f"{self._baseUrl}/chat/completions"
        lastExc: Exception | None = None

        for attempt in range(self._maxRetries + 1):
            if attempt:
                backoff = 2 ** (attempt - 1)   # 1 s, 2 s, 4 s …
                logger.warning(
                    "LLM %s attempt %d/%d — retrying in %ds after %s",
                    self._model, attempt, self._maxRetries, backoff,
                    type(lastExc).__name__,
                )
                await asyncio.sleep(backoff)

            try:
                response = await self._client.post(url, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRY_STATUSES and attempt < self._maxRetries:
                    lastExc = exc
                    continue
                raise
            except httpx.TimeoutException:
                # Timeout is not retriable — propagate immediately so the outer
                # wait_for / route handler can return 504 without wasting time.
                raise
            except httpx.RequestError as exc:
                # Transient transport errors (ConnectError, RemoteProtocolError)
                # are worth one retry.
                if attempt < self._maxRetries:
                    lastExc = exc
                    continue
                raise

        # Should be unreachable — loop always raises or returns.
        raise RuntimeError("Unreachable retry loop exit")  # pragma: no cover

    async def close(self) -> None:
        """Drain the connection pool. Called once at application shutdown."""
        await self._client.aclose()
