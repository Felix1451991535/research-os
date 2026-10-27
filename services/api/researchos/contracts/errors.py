"""Uniform error type. HTTP body = {code, message, retryable, correlation_id[, details]} (baseline §4)."""
from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def conflict(code: str, message: str, details: dict[str, Any] | None = None) -> ApiError:
    return ApiError(409, code, message, details=details)


def bad_request(code: str, message: str) -> ApiError:
    return ApiError(400, code, message)


def forbidden(code: str, message: str) -> ApiError:
    return ApiError(403, code, message)
