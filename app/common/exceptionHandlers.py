import logging
from collections.abc import Sequence
from typing import Any, cast
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from app.common.models.apiResponseModel import ApiResponse

logger = logging.getLogger(__name__)


def _clientValidationErrorResponse(errors: Sequence[Any]) -> JSONResponse:
    """
    Build a 400 JSON response for invalid client input (FastAPI request validation).

    :param errors: Pydantic validation error list from :class:`RequestValidationError`.
    :type errors: Sequence[Any]
    :return: 400 :class:`JSONResponse` with an :class:`ApiResponse` body.
    :rtype: JSONResponse
    """
    payload = ApiResponse(
        status=400,
        message="Validation failed",
        data=jsonable_encoder(errors),
    ).model_dump(mode="json")
    return JSONResponse(status_code=400, content=payload)


def _internalValidationErrorResponse() -> JSONResponse:
    """
    Build a 500 JSON response for unexpected internal Pydantic errors.

    Used for errors such as ORM-to-DTO mismatches that indicate a server-side
    bug rather than bad client input.

    :return: 500 :class:`JSONResponse` with an :class:`ApiResponse` body.
    :rtype: JSONResponse
    """
    payload = ApiResponse(
        status=500,
        message="Internal server error",
        data=None,
    ).model_dump(mode="json")
    return JSONResponse(status_code=500, content=payload)


async def _handleRequestValidationError(_request: Request, exc: Exception) -> JSONResponse:
    """
    FastAPI exception handler for :class:`RequestValidationError`.

    :param _request: Incoming request (unused).
    :type _request: Request
    :param exc: The raised :class:`RequestValidationError`.
    :type exc: Exception
    :return: 400 JSON response with structured validation errors.
    :rtype: JSONResponse
    """
    return _clientValidationErrorResponse(cast(RequestValidationError, exc).errors())


async def _handleValidationError(_request: Request, exc: Exception) -> JSONResponse:
    """
    FastAPI exception handler for unexpected internal :class:`pydantic.ValidationError`.

    :param _request: Incoming request (unused).
    :type _request: Request
    :param exc: The raised :class:`pydantic.ValidationError`.
    :type exc: Exception
    :return: 500 JSON response indicating an internal server error.
    :rtype: JSONResponse
    """
    validationExc = cast(ValidationError, exc)
    logger.error(
        "Pydantic ValidationError (often response ORM→DTO mismatch): %s",
        jsonable_encoder(validationExc.errors()),
    )
    return _internalValidationErrorResponse()


def _httpExceptionMessage(detail: Any) -> str:
    """
    Coerce an :class:`HTTPException` detail value into a plain string message.

    :param detail: The ``detail`` attribute of an :class:`HTTPException`; may be
        a ``str``, ``dict``, or any JSON-serialisable object.
    :type detail: Any
    :return: A human-readable string extracted or serialised from *detail*.
    :rtype: str
    """
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        d = cast(dict[str, Any], detail)
        rawMsg = d.get("message")
        if rawMsg is None:
            rawMsg = d.get("detail")
        if rawMsg is not None:
            return str(rawMsg)
        return str(jsonable_encoder(d))
    return str(jsonable_encoder(detail)) if detail is not None else "Error"


async def _handleHttpException(_request: Request, exc: Exception) -> JSONResponse:
    """
    FastAPI exception handler for :class:`HTTPException`.

    :param _request: Incoming request (unused).
    :type _request: Request
    :param exc: The raised :class:`HTTPException`.
    :type exc: Exception
    :return: JSON response whose HTTP status and body mirror the exception.
    :rtype: JSONResponse
    """
    httpExc = cast(HTTPException, exc)
    message = _httpExceptionMessage(httpExc.detail)
    code = httpExc.status_code
    payload = ApiResponse(status=code, message=message, data=None).model_dump(mode="json")
    return JSONResponse(status_code=code, content=payload)


def registerExceptionHandlers(app: FastAPI) -> None:
    """
    Register all global exception handlers on *app*.

    Mapping:

    - :class:`RequestValidationError` → 400 with validation detail
    - :class:`pydantic.ValidationError` → 500 (internal ORM/DTO mismatch)
    - :class:`HTTPException` → mirrored status with :class:`ApiResponse` body

    :param app: The FastAPI application instance to register handlers on.
    :type app: FastAPI
    """
    app.add_exception_handler(RequestValidationError, _handleRequestValidationError)
    app.add_exception_handler(ValidationError, _handleValidationError)
    app.add_exception_handler(HTTPException, _handleHttpException)
