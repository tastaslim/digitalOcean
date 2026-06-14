from fastapi import APIRouter, Depends

from app.common.models.apiResponseModel import ApiResponse
from app.common.utils.apiResponseUtil import successResponse
from app.infrastructure.dependencies import getMetricsService
from app.resources.metrics.metricsService import MetricsService

metricsRoute = APIRouter(prefix="/metrics", tags=["metrics"])


@metricsRoute.get("", response_model=ApiResponse[dict])
async def getMetrics(
    svc: MetricsService = Depends(getMetricsService),
) -> ApiResponse[dict]:
    """Return a real-time snapshot of proxy and shadow execution counters."""
    return successResponse(data=await svc.snapshot(), message="Metrics retrieved")
