from typing import TypeVar

from app.common.models.apiResponseModel import ApiResponse

T = TypeVar("T")


def successResponse(data: T, message: str = "Success") -> ApiResponse[T]:
    """
    Wrap *data* in a 200 :class:`ApiResponse` envelope.

    :param data: Payload to embed in the response body.
    :param message: Human-readable success label.
    :type message: str
    :return: :class:`ApiResponse` with ``status=200``.
    :rtype: ApiResponse[T]
    """
    return ApiResponse(status=200, message=message, data=data)