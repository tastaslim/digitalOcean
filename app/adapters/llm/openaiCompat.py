from typing import Any

import httpx

from app.ports.llm import LlmPort


class OpenAICompatAdapter(LlmPort):
    """
    Chat completion adapter for any OpenAI-compatible REST endpoint.

    Supported providers (all share the same /chat/completions + Bearer auth shape):
      - DigitalOcean inference  (https://inference.do-ai.run/v1)
      - OpenAI                  (https://api.openai.com/v1)
      - Groq                    (https://api.groq.com/openai/v1)
      - Together AI, Mistral, Anyscale, …

    Switching providers requires only cloud.env changes — no code changes.
    """

    def __init__(
        self,
        baseUrl: str,
        apiKey: str,
        model: str,
        timeoutSeconds: int = 30,
    ) -> None:
        self._baseUrl = baseUrl.rstrip("/")
        self._apiKey = apiKey
        self._model = model
        self._timeoutSeconds = timeoutSeconds

    @property
    def modelId(self) -> str:
        return self._model

    @property
    def timeoutSeconds(self) -> int:
        return self._timeoutSeconds

    async def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """POST to /chat/completions and return the parsed JSON body."""
        async with httpx.AsyncClient(timeout=self._timeoutSeconds) as client:
            response = await client.post(
                f"{self._baseUrl}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._apiKey}",
                    "Content-Type": "application/json",
                },
                json={"model": self._model, "messages": messages},
            )
            response.raise_for_status()
            return response.json()
