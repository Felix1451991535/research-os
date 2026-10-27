"""Tasks (K2) and approvals (K3)."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from ...contracts.errors import ApiError
from ...services.approval import approvals as svc_appr
from ...services.identity.sessions import Principal
from ...services.orchestration import tasks as svc_tasks
from ..deps import cid, db, principal, services

router = APIRouter(prefix="/api/v1", tags=["tasks"])


class TaskIn(BaseModel):
    kind: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None
    priority: int = Field(default=0, ge=-10, le=10)


class Task(BaseModel):
    id: str
    kind: str
    project_id: str | None
    parent_id: str | None
    trace_id: str
    state: str
    resource_class: str
    attempt: int
    max_attempts: int
    lease_generation: int
    stage: str | None
    progress: dict[str, Any]
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    approval_id: str | None
    cancel_requested_at: str | None
    created_at: str
    updated_at: str


class ApprovalIn(BaseModel):
    subject_type: str
    subject_id: str
    expected_hash: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)


class Approval(BaseModel):
    id: str
    subject_type: str
    subject_id: str
    subject_hash: str
    approved_by_device: str
    reauth: bool
    created_at: str
    expires_at: str
    consumed_at: str | None
    revoked_at: str | None


class SubjectHash(BaseModel):
    subject_type: str
    subject_id: str
    hash: str


@router.post("/tasks", response_model=Task, status_code=202)
def create_task(data: TaskIn, request: Request, response: Response, p: Principal = Depends(principal),
                conn: sqlite3.Connection = Depends(db)) -> Any:
    """Long work -> 202 + task. The Idempotency-Key IS the task's identity: a repeat returns the same task."""
    key = request.headers.get("idempotency-key")
    if not key:
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key header required")
    task, created = svc_tasks.create(conn, services(request).kinds, kind=data.kind, payload=data.payload,
                                     idempotency_key=key, actor=p.actor, project_id=data.project_id,
                                     priority=data.priority, correlation_id=cid(request))
    response.status_code = 202 if created else 200
    return task


@router.get("/tasks", response_model=list[Task])
def list_tasks(project_id: str | None = None, state: str | None = None, limit: int = Query(100, ge=1, le=500),
               p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> Any:
    return svc_tasks.list_tasks(conn, project_id, state, limit)


@router.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> Any:
    return svc_tasks.get(conn, task_id)


@router.post("/tasks/{task_id}/cancel", response_model=Task)
def cancel_task(task_id: str, request: Request, p: Principal = Depends(principal),
                conn: sqlite3.Connection = Depends(db)) -> Any:
    return svc_tasks.cancel(conn, task_id, p.actor, cid(request))


@router.post("/tasks/{task_id}/retry", response_model=Task)
def retry_task(task_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> Any:
    return svc_tasks.retry(conn, task_id, p.actor)


@router.get("/approvals/subject-hash", response_model=SubjectHash)
def subject_hash(subject_type: str, subject_id: str, request: Request, p: Principal = Depends(principal),
                 conn: sqlite3.Connection = Depends(db)) -> SubjectHash:
    """What the reviewer saw: pass it back as expected_hash so a changed subject is never approved blindly."""
    h = svc_appr.current_hash(conn, services(request).subjects, subject_type, subject_id)
    return SubjectHash(subject_type=subject_type, subject_id=subject_id, hash=h)


@router.post("/approvals", response_model=Approval, status_code=201)
def approve(data: ApprovalIn, request: Request, p: Principal = Depends(principal),
            conn: sqlite3.Connection = Depends(db)) -> Any:
    a = svc_appr.approve(conn, services(request).subjects, p, subject_type=data.subject_type,
                         subject_id=data.subject_id, expected_hash=data.expected_hash, scope=data.scope,
                         limits=data.limits, correlation_id=cid(request))
    return {**a, "reauth": bool(a["reauth"])}


class RevokeOut(BaseModel):
    ok: bool = True


@router.post("/approvals/{approval_id}/revoke", response_model=RevokeOut)
def revoke_approval(approval_id: str, p: Principal = Depends(principal),
                    conn: sqlite3.Connection = Depends(db)) -> RevokeOut:
    svc_appr.revoke(conn, approval_id, p)
    return RevokeOut()
