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
