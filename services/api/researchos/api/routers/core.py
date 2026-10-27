"""Health, host status, events, provider-key status, plugin listing."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ... import __version__
from ...contracts.errors import ApiError
from ...kernel.events import emit, list_events
from ...services.gateway.keystore import PROVIDER_ENV
from ...services.identity.sessions import Principal
from ...services.ops.hostinfo import host_status
from ..deps import db, principal, services

router = APIRouter(prefix="/api/v1", tags=["core"])


class Health(BaseModel):
    status: str
    version: str
    db_ok: bool


class Event(BaseModel):
    seq: int
    ts: str
    actor: str
    kind: str
    entity_type: str | None
    entity_id: str | None
    project_id: str | None
    correlation_id: str | None
    data: dict[str, Any]


class KeyIn(BaseModel):
    api_key: str | None = Field(default=None, max_length=500)


class KeyStatus(BaseModel):
    configured: bool
    source: str | None


class PluginInfo(BaseModel):
    task_kinds: list[str]
    approval_subjects: list[str]
    plugins: list[str]


@router.get("/health", response_model=Health)
def health(conn: sqlite3.Connection = Depends(db)) -> Health:
    try:
        conn.execute("SELECT 1").fetchone()
        ok = True
    except sqlite3.Error:
        ok = False
    return Health(status="ok" if ok else "degraded", version=__version__, db_ok=ok)


@router.get("/host/status")
def get_host_status(request: Request, p: Principal = Depends(principal),
                    conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return host_status(conn, services(request).settings)


@router.get("/events", response_model=list[Event])
def events(after_seq: int = 0, project_id: str | None = None, entity_id: str | None = None,
           limit: int = Query(200, ge=1, le=1000), p: Principal = Depends(principal),
           conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return list_events(conn, after_seq, limit, project_id, entity_id)


@router.get("/settings/keys", response_model=dict[str, KeyStatus])
def key_status(request: Request, p: Principal = Depends(principal)) -> dict[str, dict[str, object]]:
    """Configured yes/no and source only. Key material is never returned."""
    return services(request).keystore.status()


@router.put("/settings/keys/{provider}", response_model=KeyStatus)
def set_key(provider: str, data: KeyIn, request: Request, p: Principal = Depends(principal),
            conn: sqlite3.Connection = Depends(db)) -> dict[str, object]:
    if provider not in PROVIDER_ENV:
        raise ApiError(404, "unknown_provider", f"provider must be one of {sorted(PROVIDER_ENV)}")
    ks = services(request).keystore
    ks.set(provider, data.api_key)
    emit(conn, "settings.key_changed", actor=p.actor, entity_type="provider", entity_id=provider,
         data={"configured": bool(data.api_key)})
    return ks.status()[provider]


@router.get("/plugins", response_model=PluginInfo)
def plugins(request: Request, p: Principal = Depends(principal)) -> PluginInfo:
    svc = services(request)
    return PluginInfo(task_kinds=[k.name for k in svc.kinds.all()],
                      approval_subjects=[s.name for s in svc.subjects.all()], plugins=list(svc.plugins))
