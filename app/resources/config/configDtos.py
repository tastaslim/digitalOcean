from pydantic import BaseModel, Field


class ConfigUpdateRequest(BaseModel):
    """Payload for PUT /config."""

    shadowPercentage: float = Field(
        ge=0.0,
        le=100.0,
        description="Percentage of incoming requests to mirror to the candidate LLM (0–100).",
    )
