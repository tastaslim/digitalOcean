from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    """
    A single turn in an OpenAI-compatible conversation.

    :param role: Speaker role (``"system"``, ``"user"``, or ``"assistant"``).
    :type role: str
    :param content: Text content of the message.
    :type content: str
    """

    role: str
    content: str


class ChatRequest(BaseModel):
    """
    OpenAI-compatible chat completion request body.

    Extra fields (``temperature``, ``max_tokens``, ``stream``, etc.) are
    forwarded to the upstream LLM without modification.

    :param messages: Ordered list of conversation turns.
    :type messages: list[ChatMessage]
    """

    model_config = ConfigDict(extra="allow")

    messages: list[ChatMessage]
