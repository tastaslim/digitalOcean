from fastapi import APIRouter
from app.common.models.apiResponseModel import ApiResponse
from app.common.utils.apiResponseUtil import successResponse
from app.resources.metrics.metricsService import metricsStore

metricsRoute = APIRouter(prefix="/metrics", tags=["metrics"])


@metricsRoute.get("", response_model=ApiResponse[dict])
async def getMetrics() -> ApiResponse[dict]:
    """
    Return a real-time snapshot of proxy and shadow execution counters.

    :return: Current metrics wrapped in an :class:`ApiResponse` envelope.
    :rtype: ApiResponse[dict]
    """
    return successResponse(data=metricsStore.snapshot(), message="Metrics retrieved")
