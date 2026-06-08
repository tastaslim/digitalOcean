from pydantic import BaseModel, Field


class ConfigUpdateRequest(BaseModel):
    """
    Request body for ``PUT /config``.

    :param shadowPercentage: Percentage of incoming requests to mirror to the
        candidate LLM.  Must be between ``0.0`` (no shadowing) and ``100.0``
        (all requests shadowed).
    :type shadowPercentage: float
    """

    shadowPercentage: float = Field(
        ge=0.0,
        le=100.0,
        description="Percentage of incoming requests to mirror to the candidate LLM (0–100).",
    )
