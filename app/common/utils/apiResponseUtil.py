from typing import TypeVar
from fastapi.responses import JSONResponse
from app.common.models.apiResponseModel import ApiResponse
T = TypeVar("T")

def successResponse(data: T, message: str = "Success") -> ApiResponse[T]:
    return ApiResponse(status=200, message=message, data=data)

def errorResponse(message: str = "Error", status: int = 500) -> ApiResponse[None]:
    return ApiResponse(status=status, message=message, data=None)

def toJsonResponse(response: ApiResponse[T]) -> JSONResponse:
    """Serialize ``ApiResponse`` and set the HTTP status from ``response.status``."""
    return JSONResponse(
        content=response.model_dump(mode="json"),
        status_code=response.status,
    )