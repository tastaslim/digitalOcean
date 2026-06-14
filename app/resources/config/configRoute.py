from fastapi import APIRouter, Depends

from app.common.models.apiResponseModel import ApiResponse
from app.common.utils.apiResponseUtil import successResponse
from app.infrastructure.dependencies import getConfigService
from app.resources.config.configDtos import ConfigUpdateRequest
from app.resources.config.configService import ConfigService

configRoute = APIRouter(prefix="/config", tags=["config"])


@configRoute.put("", response_model=ApiResponse[dict])
async def updateConfig(
    request: ConfigUpdateRequest,
    svc: ConfigService = Depends(getConfigService),
) -> ApiResponse[dict]:
    """Update the shadow routing percentage at runtime without restarting the service."""
    await svc.update(shadowPercentage=request.shadowPercentage)
    return successResponse(data=await svc.snapshot(), message="Config updated")
