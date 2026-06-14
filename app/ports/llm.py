from abc import ABC, abstractmethod
from typing import Any


class LlmPort(ABC):
    """
    Provider-agnostic interface for chat completion calls.

    Implementations:
      - OpenAICompatAdapter — any OpenAI-compatible endpoint (DigitalOcean inference,
        OpenAI, Groq, Together, Mistral, etc.)

    Swap providers by changing *_LLM_BASE_URL / *_LLM_API_KEY / *_LLM_MODEL in
    cloud.env and restarting. No application code changes required.
    """

    @property
    @abstractmethod
    def modelId(self) -> str:
        """The model identifier sent in every request payload."""

    @property
    @abstractmethod
    def timeoutSeconds(self) -> int:
        """Hard deadline for a single chat call (used by callers for wait_for)."""

    @abstractmethod
    async def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Send a chat completion request and return the raw provider JSON response.

        :raises httpx.HTTPStatusError: Non-2xx from the provider.
        :raises httpx.RequestError:    Transport-level failure.
        :raises asyncio.TimeoutError:  Caller-supplied wait_for deadline exceeded.
        """
