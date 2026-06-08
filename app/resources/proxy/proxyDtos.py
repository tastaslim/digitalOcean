from typing import Any
from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    """A single message in an OpenAI-compatible conversation turn."""
    role: str
    content: str


class ChatRequest(BaseModel):
    """
    OpenAI-compatible chat completion request.
    Extra fields (temperature, max_tokens, stream, etc.) are forwarded to the upstream LLM as-is.
    """
    model_config = ConfigDict(extra="allow")

    messages: list[ChatMessage]

    def toPayload(self, model: str) -> dict[str, Any]:
        """
        Serialize the request to a dict and inject the target model name.

        Args:
            model: Model identifier to set on the outgoing payload.

        Returns:
            Dict ready to be JSON-encoded and sent to the LLM endpoint.
        """
        payload = self.model_dump(exclude_none=True)
        payload["model"] = model
        return payload
