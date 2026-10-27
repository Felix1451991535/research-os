"""One-click update (ADR-223): status, explicit check, download + verify, install, mirror setting. No route checks
for updates by itself; every network access starts from an owner click."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...contracts.errors import ApiError
from ...services.identity.sessions import Principal
from ...services.ops.updater import UpdateError, Updater
from ..deps import principal

router = APIRouter(prefix="/api/v1/update", tags=["update"])

_STATUS = {"update_bad_mirror": 400, "update_not_available": 409, "update_busy": 409, "update_not_ready": 409,
           "update_unsupported": 409, "update_launch_failed": 500}
_RETRYABLE = {"update_network", "update_timeout", "update_http_error"}


class MirrorIn(BaseModel):
    mirror_prefix: str = Field(default="", max_length=300)


def _updater(request: Request) -> Updater:
    u: Updater = request.app.state.updater
    return u


def _call(fn: Any) -> dict[str, Any]:
    try:
        out: dict[str, Any] = fn()
        return out
    except UpdateError as e:
        raise ApiError(_STATUS.get(e.code, 502), e.code, e.message, retryable=e.code in _RETRYABLE) from e


@router.get("")
def status(request: Request, p: Principal = Depends(principal)) -> dict[str, Any]:
    return _updater(request).status()


@router.post("/check")
def check(request: Request, p: Principal = Depends(principal)) -> dict[str, Any]:
    return _call(_updater(request).check)


@router.post("/download", status_code=202)
def download(request: Request, p: Principal = Depends(principal)) -> dict[str, Any]:
    return _call(_updater(request).start_download)


@router.post("/install", status_code=202)
def install(request: Request, p: Principal = Depends(principal)) -> dict[str, Any]:
    return _call(_updater(request).install)


@router.put("/settings")
def settings(data: MirrorIn, request: Request, p: Principal = Depends(principal)) -> dict[str, Any]:
    u = _updater(request)

    def save() -> dict[str, Any]:
        u.set_mirror_prefix(data.mirror_prefix)
        return u.status()
    return _call(save)
