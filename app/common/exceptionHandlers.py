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
    """Invalid query/path/body from the client (FastAPI request validation)."""
    payload = ApiResponse(
        status=400,
        message="Validation failed",
        data=jsonable_encoder(errors),
    ).model_dump(mode="json")
    return JSONResponse(status_code=400, content=payload)


def _internalValidationErrorResponse() -> JSONResponse:
    """Unexpected Pydantic errors (e.g. ORM → DTO mismatch): treat as server error, not client 400."""
    payload = ApiResponse(
        status=500,
        message="Internal server error",
        data=None,
    ).model_dump(mode="json")
    return JSONResponse(status_code=500, content=payload)


async def _handleRequestValidationError(_request: Request, exc: Exception) -> JSONResponse:
    return _clientValidationErrorResponse(cast(RequestValidationError, exc).errors())


async def _handleValidationError(_request: Request, exc: Exception) -> JSONResponse:
    validationExc = cast(ValidationError, exc)
    logger.error(
        "Pydantic ValidationError (often response ORM→DTO mismatch): %s",
        jsonable_encoder(validationExc.errors()),
    )
    return _internalValidationErrorResponse()


def _httpExceptionMessage(detail: Any) -> str:
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
    httpExc = cast(HTTPException, exc)
    message = _httpExceptionMessage(httpExc.detail)
    code = httpExc.status_code
    payload = ApiResponse(status=code, message=message, data=None).model_dump(mode="json")
    return JSONResponse(status_code=code, content=payload)


def registerExceptionHandlers(app: FastAPI) -> None:
    """Request validation → 400; other Pydantic errors → 500; HTTPException → ApiResponse body + status."""
    app.add_exception_handler(RequestValidationError, _handleRequestValidationError)
    app.add_exception_handler(ValidationError, _handleValidationError)
    app.add_exception_handler(HTTPException, _handleHttpException)
