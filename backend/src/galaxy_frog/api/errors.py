"""Stable API exceptions and handlers with safe public messages."""

import logging
from http import HTTPStatus
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import JsonValue
from starlette.exceptions import HTTPException

from galaxy_frog.api.middleware import CORRELATION_ID_HEADER
from galaxy_frog.api.schemas import ErrorPayload, ErrorResponse

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """A deliberate API failure whose public fields are safe to serialize."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool,
        suggested_action: str | None = None,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.suggested_action = suggested_action
        self.details = details


def correlation_id_from_request(request: Request) -> str:
    """Read the middleware identifier or safely recover when middleware did not run."""

    value = getattr(request.state, "correlation_id", None)
    return value if isinstance(value, str) else str(uuid4())


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    suggested_action: str | None = None,
    details: dict[str, JsonValue] | None = None,
) -> JSONResponse:
    """Build the canonical envelope and repeat its correlation ID as a header."""

    correlation_id = correlation_id_from_request(request)
    body = ErrorResponse(
        error=ErrorPayload(
            code=code,
            message=message,
            correlation_id=correlation_id,
            retryable=retryable,
            suggested_action=suggested_action,
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True),
        headers={CORRELATION_ID_HEADER: correlation_id},
    )


async def handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    """Serialize an intentional application error."""

    api_error = cast(ApiError, exc)
    return error_response(
        request,
        status_code=api_error.status_code,
        code=api_error.code,
        message=api_error.message,
        retryable=api_error.retryable,
        suggested_action=api_error.suggested_action,
        details=api_error.details,
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Return validation locations without echoing submitted values."""

    validation_error = cast(RequestValidationError, exc)
    errors: list[JsonValue] = [
        {
            "location": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in validation_error.errors()
    ]
    return error_response(
        request,
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        code="INVALID_INPUT",
        message="The request did not satisfy the API contract.",
        retryable=False,
        suggested_action="Correct the indicated request fields and try again.",
        details={"errors": errors},
    )


async def handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    """Normalize framework HTTP errors without serializing unsafe objects."""

    http_error = cast(HTTPException, exc)
    detail = cast(object, http_error.detail)
    message = detail if isinstance(detail, str) else "The request could not be completed."
    code = "NOT_FOUND" if http_error.status_code == HTTPStatus.NOT_FOUND else "HTTP_ERROR"
    return error_response(
        request,
        status_code=http_error.status_code,
        code=code,
        message=message,
        retryable=http_error.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR,
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected failures while returning no internal exception details."""

    correlation_id = correlation_id_from_request(request)
    logger.exception("Unhandled API error", extra={"correlation_id": correlation_id}, exc_info=exc)
    request.state.correlation_id = correlation_id
    return error_response(
        request,
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred.",
        retryable=False,
        suggested_action="Retry later or contact support with the correlation ID.",
    )


def register_error_handlers(application: FastAPI) -> None:
    """Register one canonical handler for each FastAPI failure category."""

    application.add_exception_handler(ApiError, handle_api_error)
    application.add_exception_handler(RequestValidationError, handle_validation_error)
    application.add_exception_handler(HTTPException, handle_http_error)
    application.add_exception_handler(Exception, handle_unexpected_error)
