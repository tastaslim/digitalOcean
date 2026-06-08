from typing import Generic, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """
    Generic envelope returned by every endpoint.

    :param status: HTTP status code mirrored in the response body.
    :type status: int
    :param message: Human-readable result summary.
    :type message: str
    :param data: Optional payload; ``None`` for error responses.
    :type data: T or None
    """

    status: int
    message: str
    data: Optional[T] = None