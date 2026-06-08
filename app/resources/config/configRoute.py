from fastapi import APIRouter
from app.common.models.apiResponseModel import ApiResponse
from app.common.utils.apiResponseUtil import successResponse
from app.resources.config.configDtos import ConfigUpdateRequest
from app.resources.config.configService import runtimeConfig

configRoute = APIRouter(prefix="/config", tags=["config"])


@configRoute.put("", response_model=ApiResponse[dict])
async def updateConfig(request: ConfigUpdateRequest) -> ApiResponse[dict]:
    """
    Update the shadow routing percentage at runtime without restarting the service.

    Args:
        request: New configuration values (shadowPercentage: 0–100).

    Returns:
        The applied configuration snapshot wrapped in ApiResponse.
    """
    await runtimeConfig.update(shadowPercentage=request.shadowPercentage)
    return successResponse(data=runtimeConfig.snapshot(), message="Config updated")
