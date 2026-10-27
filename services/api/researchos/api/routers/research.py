"""Cited question answering and decision records."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...contracts.errors import not_found
from ...kernel.db import jload, row, rows
from ...services import projects as svc_projects
from ...services.identity.sessions import Principal
from ...services.research import decisions
from ...services.research.qa import QAContext, answer
from ..deps import cid, db, idempotent, idempotent_long, principal, services

router = APIRouter(prefix="/api/v1", tags=["research"])


class QuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    project_id: str | None = None
    force_top: bool = False


def _out(a: dict[str, Any]) -> dict[str, Any]:
    body = jload(a["body"]) or {}
    return {"id": a["id"], "status": a["status"], "question": a["question"], "project_id": a["project_id"],
            "created_at": a["created_at"], "trust": jload(a["trust_composition"]) or {},
            "call_ids": jload(a["call_ids"]) or [], **body}


@router.post("/answers", status_code=201)
def ask(data: QuestionIn, request: Request, p: Principal = Depends(principal),
        conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """A repeated submit with the same Idempotency-Key returns the first answer (no second paid call)."""
    def run() -> tuple[int, Any]:
        ctx = QAContext(data.question, data.project_id, p.actor, cid(request), data.force_top)
        res = answer(conn, services(request).gateway, ctx)
        return 201, _out(row(conn.execute("SELECT * FROM answers WHERE id=?", (res["id"],))) or {})

    _, body = idempotent_long(request, conn, "answers.create", data.model_dump(), run)
    out: dict[str, Any] = body
    return out


@router.get("/answers")
def list_answers(project_id: str | None = None, p: Principal = Depends(principal),
                 conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM answers" + (" WHERE project_id=?" if project_id else "") + " ORDER BY created_at DESC LIMIT 50"
    return [_out(a) for a in rows(conn.execute(sql, (project_id,) if project_id else ()))]


@router.get("/answers/{answer_id}")
def get_answer(answer_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
               ) -> dict[str, Any]:
    a = row(conn.execute("SELECT * FROM answers WHERE id=?", (answer_id,)))
    if a is None:
        raise not_found("answer")
    return _out(a)


class Alternative(BaseModel):
    option: str = Field(min_length=1, max_length=500)
    why_not: str = Field(min_length=1, max_length=2000)


class DecisionIn(BaseModel):
    kind: str
    question: str = Field(min_length=3, max_length=1000)
    choice: str = Field(min_length=1, max_length=1000)
    alternatives: list[Alternative] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=3, max_length=5000)
    links: list[str] = Field(default_factory=list, max_length=50)
    supersedes: str | None = None


@router.post("/projects/{project_id}/decisions", status_code=201)
def decision_create(project_id: str, data: DecisionIn, request: Request, p: Principal = Depends(principal),
                    conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Owner-written decision record linked to evidence / claims / runs; immutable, revised by superseding."""
    svc_projects.get(conn, project_id)
    _, body = idempotent(request, conn, "decisions.create", {"project_id": project_id, **data.model_dump()},
                         lambda: (201, decisions.create(conn, project_id, data.model_dump(), p.actor,
                                                        supersedes=data.supersedes)))
    out: dict[str, Any] = body
    return out


@router.get("/projects/{project_id}/decisions")
def decision_list(project_id: str, include_superseded: bool = False, p: Principal = Depends(principal),
                  conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    svc_projects.get(conn, project_id)
    return decisions.listing(conn, project_id, include_superseded)


@router.get("/decisions/{decision_id}")
def decision_get(decision_id: str, p: Principal = Depends(principal),
                 conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return decisions.get(conn, decision_id)
