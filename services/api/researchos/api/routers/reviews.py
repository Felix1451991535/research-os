"""Systematic-review screening (PRISMA) and saved searches over HTTP."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...kernel.db import rows
from ...services.identity.sessions import Principal
from ...services.literature import monitor, screening, screening_model
from ...services.orchestration import tasks as task_svc
from ..deps import db, principal, services

router = APIRouter(prefix="/api/v1", tags=["literature"])


class ReviewIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=3, max_length=2000)
    criteria: dict[str, Any]
    project_id: str | None = None


class ItemsIn(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=1000)
    search_run_id: str | None = None


class DecisionIn(BaseModel):
    candidate_id: str
    stage: str = Field(pattern="^(title_abstract|full_text)$")
    decision: str = Field(pattern="^(include|exclude|maybe)$")
    reason: str = Field(min_length=3, max_length=500)


class SuggestIn(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=200)
    use_model: bool = False


class EnableIn(BaseModel):
    enabled: bool


@router.post("/reviews", status_code=201)
def create_review(
    data: ReviewIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    return screening.create(
        conn,
        title=data.title,
        question=data.question,
        criteria=data.criteria,
        actor=p.actor,
        project_id=data.project_id,
    )


@router.get("/reviews/{review_id}")
def get_review(
    review_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    return {**screening.get(conn, review_id), "items": screening.items(conn, review_id)}


@router.post("/reviews/{review_id}/items")
def add_items(
    review_id: str, data: ItemsIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    return {"added": screening.add_items(conn, review_id, data.candidate_ids, data.search_run_id)}


@router.post("/reviews/{review_id}/decisions", status_code=201)
def decide(
    review_id: str, data: DecisionIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    """Only users decide; agents/models have no route here (they can only suggest)."""
    return {"id": screening.decide(conn, review_id, data.candidate_id, data.stage, data.decision, data.reason, p.actor)}


@router.post("/reviews/{review_id}/suggestions")
def suggest(
    review_id: str,
    data: SuggestIn,
    request: Request,
    p: Principal = Depends(principal),
    conn: sqlite3.Connection = Depends(db),
) -> dict[str, Any]:
    out: dict[str, Any] = {"rules": screening.auto_screen(conn, review_id, data.candidate_ids, p.actor)}
    if data.use_model:
        out["model"] = screening_model.suggest(conn, services(request).gateway, review_id, data.candidate_ids, p.actor)
    return out


@router.get("/reviews/{review_id}/prisma")
def prisma(review_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return screening.prisma(conn, review_id)


@router.post("/saved-searches", status_code=201)
def create_saved(
    data: dict[str, Any], request: Request, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    return monitor.create(conn, data, p.actor, sorted(services(request).extensions.sources))


@router.get("/saved-searches")
def list_saved(p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return [monitor.get(conn, r["id"]) for r in rows(conn.execute("SELECT id FROM saved_searches ORDER BY created_at"))]


@router.put("/saved-searches/{sid}/enabled")
def enable_saved(
    sid: str, data: EnableIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    return monitor.set_enabled(conn, sid, data.enabled, p.actor)


@router.post("/saved-searches/{sid}/run", status_code=202)
def run_now(
    sid: str, request: Request, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    s = monitor.get(conn, sid)
    key = request.headers.get("idempotency-key") or f"ss:{sid}:manual:{s['last_run_at']}"
    t, _ = task_svc.create(
        conn,
        services(request).kinds,
        kind="literature.saved_search",
        payload={"saved_search_id": sid},
        actor=p.actor,
        idempotency_key=key,
        project_id=s["project_id"],
    )
    return t
