from typing import TypeVar
from app.common.models.apiResponseModel import ApiResponse

T = TypeVar("T")


def successResponse(data: T, message: str = "Success") -> ApiResponse[T]:
    return ApiResponse(status=200, message=message, data=data)