"""K4 Effects over HTTP: owner file roots, library-organisation plans, apply/resume/undo (as tasks), journal.
There is no route for agents: proposing is allowed, applying needs the owner's approval of the exact plan hash."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...contracts.errors import ApiError
from ...kernel.db import row, rows
from ...services.effects import apply as effects_apply
from ...services.effects import journal, plan, roots
from ...services.identity.sessions import Principal
from ...services.orchestration import tasks as task_svc
from ..deps import db, principal, services

router = APIRouter(prefix="/api/v1", tags=["effects"])


class RootsIn(BaseModel):
    roots: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class OrganizeIn(BaseModel):
    source_dir: str = Field(min_length=1, max_length=1000)
    library_dir: str = Field(min_length=1, max_length=1000)
    mode: str = Field(default="copy", pattern="^(copy|move)$")
    project_id: str | None = None


@router.get("/settings/file-roots")
def get_roots(p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return roots.get_roots(conn)


@router.put("/settings/file-roots")
def put_roots(data: RootsIn, request: Request, p: Principal = Depends(principal),
              conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return roots.set_roots(conn, services(request).settings.data_dir, data.roots, p.actor)


@router.post("/library/organize", status_code=202)
def organize(data: OrganizeIn, request: Request, p: Principal = Depends(principal),
             conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    key = request.headers.get("idempotency-key")
    if not key:
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key header required")
    t, _ = task_svc.create(conn, services(request).kinds, kind="library.organize", payload=data.model_dump(),
                           actor=p.actor, idempotency_key=key, project_id=data.project_id)
    return t


def _running(conn: sqlite3.Connection, pid: str) -> bool:
    return row(conn.execute("SELECT 1 FROM tasks WHERE kind IN ('effects.apply','effects.undo') AND "
                            "json_extract(payload,'$.plan_id')=? AND state IN ('queued','running','cancelling')",
                            (pid,))) is not None


@router.get("/effects")
def list_plans(p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return rows(conn.execute("SELECT id, kind, title, state, plan_hash, created_at, updated_at FROM effect_plans "
                             "ORDER BY created_at DESC LIMIT 200"))


@router.get("/effects/{pid}")
def get_plan(pid: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    pl = plan.get(conn, pid)
    busy = _running(conn, pid)
    return {**pl, "journal": journal.entries(conn, pid), "busy": busy,
            "needs_recovery": pl["state"] in ("applying", "undoing") and not busy}


def _enqueue(conn: sqlite3.Connection, request: Request, kind: str, pid: str, actor: str) -> dict[str, Any]:
    if _running(conn, pid):
        raise ApiError(409, "busy", "an apply/undo for this plan is already queued or running")
    n = row(conn.execute("SELECT COUNT(*) AS n FROM effect_journal WHERE plan_id=?", (pid,)))
    t, _ = task_svc.create(conn, services(request).kinds, kind=kind, payload={"plan_id": pid}, actor=actor,
                           idempotency_key=f"{kind}:{pid}:{(n or {}).get('n', 0)}")
    return t


@router.post("/effects/{pid}/apply", status_code=202)
def apply_plan(pid: str, request: Request, p: Principal = Depends(principal),
               conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Also the owner's confirmation to resume after an interruption or a paused (locked-file) op."""
    pl = plan.get(conn, pid)
    if not effects_apply.approved(conn, pl):
        raise ApiError(409, "effect_not_approved", "approve this exact plan first",
                       details={"subject_type": "effect_plan", "subject_id": pid})
    return _enqueue(conn, request, "effects.apply", pid, p.actor)


@router.post("/effects/{pid}/undo", status_code=202)
def undo_plan(pid: str, request: Request, p: Principal = Depends(principal),
              conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return _enqueue(conn, request, "effects.undo", pid, p.actor)
