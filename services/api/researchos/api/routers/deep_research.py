"""Deep research pipeline and claim graph over HTTP."""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...kernel.db import rows
from ...services import projects as svc_projects
from ...services.identity.sessions import Principal
from ...services.literature import screening
from ...services.research import claims, deep, deep_tasks, gaps
from ...services.research import deep_store as store
from ..deps import db, principal, services

router = APIRouter(prefix="/api/v1", tags=["research"])


class DeepIn(BaseModel):
    project_id: str
    question: str = Field(min_length=5, max_length=1000)
    sources: list[str] = Field(min_length=1, max_length=10)
    sub_questions: list[dict[str, Any]] | None = None
    rounds: int = Field(default=2, ge=1, le=2)
    page_size: int = Field(default=10, ge=1, le=50)
    use_model_screening: bool = False
    criteria: dict[str, Any] | None = None


class ContradictionIn(BaseModel):
    a: str
    b: str
    status: str = Field(pattern="^(confirmed|rejected)$")


def _view(conn: sqlite3.Connection, r: dict[str, Any]) -> dict[str, Any]:
    checkpoints = {"plan": {"subject_type": "research_checkpoint", "subject_id": f"{r['id']}:plan"}}
    if r["data"].get("selection_hash"):
        checkpoints["import"] = {"subject_type": "knowledge_import", "subject_id": r["data"]["selection_hash"]}
    return {**r, "checkpoints": checkpoints, "prisma": screening.prisma(conn, r["review_id"])}


@router.post("/research/deep", status_code=201)
def create(data: DeepIn, request: Request, p: Principal = Depends(principal),
           conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    svc = services(request)
    r = deep.create(conn, svc, project_id=data.project_id, question=data.question, sources=data.sources,
                    actor=p.actor, sub_questions=data.sub_questions, rounds=data.rounds, page_size=data.page_size,
                    use_model_screening=data.use_model_screening, criteria=data.criteria)
    deep_tasks.enqueue(conn, svc.kinds, r["id"], "created", p.actor)
    return _view(conn, r)


@router.get("/research/deep/{rid}")
def get(rid: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return _view(conn, store.get(conn, rid))


@router.post("/research/deep/{rid}/advance", status_code=202)
def advance(rid: str, request: Request, p: Principal = Depends(principal),
            conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    store.get(conn, rid)
    return deep_tasks.enqueue(conn, services(request).kinds, rid, uuid.uuid4().hex, p.actor)


@router.post("/research/deep/{rid}/accept-suggestions")
def accept(rid: str, request: Request, p: Principal = Depends(principal),
           conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Owner action: adopt the current suggestions as the owner's own screening decisions."""
    n = deep.accept_suggestions(conn, rid, p.actor)
    deep_tasks.enqueue(conn, services(request).kinds, rid, uuid.uuid4().hex, p.actor)
    return {"decided": n}


@router.get("/claims")
def list_claims(project_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                ) -> dict[str, Any]:
    ids = [r["id"] for r in rows(conn.execute(
        "SELECT id FROM entities WHERE type='claim' AND project_id=? ORDER BY created_at", (project_id,)))]
    return {"claims": claims.get_many(conn, ids), "contradictions": claims.contradictions(conn, ids)}


@router.post("/claims/contradictions/review")
def review(data: ContradictionIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
           ) -> dict[str, Any]:
    return claims.review_contradiction(conn, data.a, data.b, data.status, p.actor)


class GapMapIn(BaseModel):
    project_id: str
    axes: dict[str, list[str | list[str]]]


@router.post("/claims/gap-map")
def gap_map(data: GapMapIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
            ) -> dict[str, Any]:
    """Evidence-gap map over the project's verified claims (no model call): status per axis-term cell."""
    svc_projects.get(conn, data.project_id)
    return gaps.gap_map(conn, data.project_id, data.axes)
