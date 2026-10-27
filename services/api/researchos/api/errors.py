"""HTTP mapping of errors: {code, message, retryable, correlation_id[, details]}."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..contracts.errors import ApiError

log = logging.getLogger("researchos")


def error_body(code: str, message: str, retryable: bool, cid: str, details: dict[str, Any] | None = None
               ) -> dict[str, Any]:
    b: dict[str, Any] = {"code": code, "message": message, "retryable": retryable, "correlation_id": cid}
    if details:
        b["details"] = details
    return b


def _cid(request: Request) -> str:
    return str(getattr(request.state, "correlation_id", "") or "")


async def _api(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(error_body(exc.code, exc.message, exc.retryable, _cid(request), exc.details), exc.status)


async def _val(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse(error_body("validation_error", "request validation failed", False, _cid(request),
                                   {"errors": errs}), 422)


async def _http(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return JSONResponse(error_body(code, str(exc.detail), False, _cid(request)), exc.status_code)


async def _any(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error cid=%s", _cid(request))
    return JSONResponse(error_body("internal_error", "internal error; see server log with correlation_id", True,
                                   _cid(request)), 500)


def install(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api)
    app.add_exception_handler(RequestValidationError, _val)
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(Exception, _any)
