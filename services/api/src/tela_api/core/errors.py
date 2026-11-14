"""Error envelope: {"error": {"code", "message", "request_id", "details"}} (api_catalog Conventions).

Messages never include stack traces, SQL, credentials or internal hostnames.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from tela_common.errors import ErrorCode, TelaError
from tela_common.logging import get_logger

log = get_logger(__name__)

_HTTP_CODES = {
    401: ErrorCode.TOKEN_INVALID,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    412: ErrorCode.PRECONDITION_FAILED,
    429: ErrorCode.RATE_LIMITED,
}


def _body(code: str, message: str, request: Request, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
            "details": details or {},
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(TelaError)
    async def _tela(request: Request, exc: TelaError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.code == ErrorCode.TOKEN_INVALID else None
        if exc.code == ErrorCode.RATE_LIMITED and "retry_after_s" in exc.details:
            headers = {"Retry-After": str(int(exc.details["retry_after_s"]))}
        details = dict(exc.details)
        # Rejection internals (offending names, compiler detail) are for Admins only (US-018).
        principal = getattr(request.state, "principal", None)
        if "admin_detail" in details and getattr(principal, "level", None) != "admin":
            details.pop("admin_detail")
        return JSONResponse(_body(exc.code, exc.message, request, details), status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = {"errors": [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]}
        return JSONResponse(
            _body(ErrorCode.VALIDATION_ERROR, "The request is not valid.", request, details), status_code=400
        )

    @app.exception_handler(ValidationError)
    async def _model_validation(request: Request, exc: ValidationError) -> JSONResponse:
        # Raised when a handler re-validates merged data (e.g. PATCH on top of a stored row).
        details = {"errors": [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]}
        return JSONResponse(
            _body(ErrorCode.VALIDATION_ERROR, "The request is not valid.", request, details), status_code=400
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, ErrorCode.VALIDATION_ERROR)
        return JSONResponse(_body(code, str(exc.detail), request), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            _body(ErrorCode.INTERNAL, "Something went wrong. Quote the request id to support.", request),
            status_code=500,
        )
