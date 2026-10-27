"""Authored documents (reports, manuscripts): create, edit with revisions, live gate check, gated export."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ...contracts.errors import ApiError
from ...kernel.db import tx
from ...kernel.idempotency import request_hash
from ...platform.sysinfo import gpu_info
from ...services import projects as svc_projects
from ...services.content import docs
from ...services.identity.sessions import Principal
from ...services.writing import doc_export, feasibility, insert, methods_checklist, paper_card, suggestions
from ..deps import cid, db, idempotent, idempotent_long, principal, services

router = APIRouter(prefix="/api/v1", tags=["docs"])


class DocIn(BaseModel):
    kind: str = Field(pattern="^(report|manuscript)$")
    title: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=1_000_000)


class DocUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=1_000_000)
    base_version: int


class ExportIn(BaseModel):
    exporter: str = Field(min_length=1, max_length=80)
    mode: str = Field(default="final", pattern="^(draft|final)$")
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/projects/{project_id}/docs", status_code=201)
def create_doc(project_id: str, data: DocIn, request: Request, p: Principal = Depends(principal),
               conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    svc_projects.get(conn, project_id)

    def run() -> tuple[int, Any]:
        with tx(conn, join=True):
            return 201, docs.create_in_tx(conn, services(request).doc_kinds, project_id, data.kind, data.title,
                                          data.body, p.actor, cid=cid(request))

    return _once(request, conn, "docs.create", {"project_id": project_id, **data.model_dump()}, run)


def _once(request: Request, conn: sqlite3.Connection, scope: str, payload: dict[str, Any],
          fn: Callable[[], tuple[int, Any]]) -> dict[str, Any]:
    """Doc writes honour the client's Idempotency-Key: a retried intent replays the first response (no second
    revision, export or decision)."""
    def run() -> tuple[int, Any]:
        try:
            return fn()
        except ApiError as exc:
            if scope != "docs.update" or exc.code != "version_conflict":
                raise
            # Preserve the existing conflict copy and record the error in the same transaction.
            return exc.status, {"code": exc.code, "message": exc.message, "details": exc.details}

    status, body = idempotent(request, conn, scope, payload, run)
    if status >= 400:
        raise ApiError(status, body["code"], body["message"], details=body["details"])
    out: dict[str, Any] = body
    return out


@router.get("/projects/{project_id}/docs")
def list_docs(project_id: str, kind: str | None = None, p: Principal = Depends(principal),
              conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return docs.list_for_project(conn, project_id, kind)


@router.get("/docs/{doc_id}")
def get_doc(doc_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return docs.get(conn, doc_id)


@router.put("/docs/{doc_id}")
def update_doc(doc_id: str, data: DocUpdate, request: Request, p: Principal = Depends(principal),
               conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    def run() -> tuple[int, Any]:
        cur = docs.get(conn, doc_id)
        return 200, dict(docs.update(conn, doc_id, title=cur["title"] if data.title is None else data.title,
                                     body=cur["body"] if data.body is None else data.body,
                                     base_version=data.base_version, actor=p.actor, client_operation_id=None,
                                     cid=cid(request)))

    return _once(request, conn, "docs.update", {"doc_id": doc_id, **data.model_dump()}, run)


@router.get("/docs/{doc_id}/check")
def check_doc(doc_id: str, mode: str = Query("final", pattern="^(draft|final)$"), p: Principal = Depends(principal),
              conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return doc_export.check(conn, doc_id, mode)  # type: ignore[arg-type]


@router.post("/docs/{doc_id}/export")
def export_doc(doc_id: str, data: ExportIn, request: Request, p: Principal = Depends(principal),
               conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    svc = services(request)
    exp = svc.extensions.exporters.get(data.exporter)
    if exp is None:
        raise ApiError(404, "unknown_exporter", f"no exporter {data.exporter!r}",
                       details={"available": sorted(svc.extensions.exporters)})
    def run() -> tuple[int, Any]:
        return 200, doc_export.export(conn, svc.settings.data_dir, exp, doc_id, data.mode,  # type: ignore[arg-type]
                                      data.options, p.actor, export_id="exp_" + request_hash(
                                          ["docs.export", request.headers.get("idempotency-key")])[:24])

    return _once(request, conn, "docs.export", {"doc_id": doc_id, **data.model_dump()}, run)


class SuggestionIn(BaseModel):
    op: str = Field(pattern="^(replace|insert_after|append)$")
    text: str = Field(min_length=1, max_length=200_000)
    anchor: str | None = Field(default=None, max_length=20_000)
    rationale: str = Field(default="", max_length=2000)


class AnswerIn(BaseModel):
    answer_id: str
    heading: str | None = Field(default=None, max_length=300)


class DecisionIn(BaseModel):
    accept: bool


@router.post("/docs/{doc_id}/suggestions", status_code=201)
def propose(doc_id: str, data: SuggestionIn, p: Principal = Depends(principal),
            conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return suggestions.propose(conn, doc_id, op=data.op, text=data.text, anchor=data.anchor, actor=p.actor,
                               rationale=data.rationale)


@router.get("/docs/{doc_id}/suggestions")
def list_suggestions(doc_id: str, status: str | None = None, p: Principal = Depends(principal),
                     conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return suggestions.list_for(conn, doc_id, status)


@router.post("/docs/{doc_id}/suggestions/from-answer", status_code=201)
def suggest_answer(doc_id: str, data: AnswerIn, p: Principal = Depends(principal),
                   conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Proposes the verified, cited sentences of a QA answer as an appended section (owner accepts or rejects)."""
    text = insert.answer_markdown(conn, data.answer_id, data.heading)
    return suggestions.propose(conn, doc_id, op="append", text=text, actor=p.actor,
                               rationale="cited answer (verified sentences only)", source={"answer_id": data.answer_id})


@router.post("/suggestions/{sid}/decision")
def decide(sid: str, data: DecisionIn, request: Request, p: Principal = Depends(principal),
           conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return _once(request, conn, "suggestions.decide", {"sid": sid, **data.model_dump()},
                 lambda: (200, suggestions.decide(conn, sid, data.accept, p.actor)))


class CardIn(BaseModel):
    project_id: str


def _card(request: Request, conn: sqlite3.Connection, kind: str, project_id: str, payload: dict[str, Any],
          make: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    svc_projects.get(conn, project_id)
    # paid QA calls: a retried intent (same Idempotency-Key) replays the first card instead of building a second
    _, body = idempotent_long(request, conn, f"docs.{kind}", payload,
                              lambda progress, checkpoint: (201, make(progress=progress, checkpoint=checkpoint)),
                              resumable=True)
    out: dict[str, Any] = body
    return out


def _work_card(request: Request, conn: sqlite3.Connection, p: Principal, module: Any, work_id: str,
               data: CardIn) -> dict[str, Any]:
    svc = services(request)
    return _card(request, conn, module.SPEC.kind, data.project_id, {"work_id": work_id, **data.model_dump()},
                 partial(module.create, conn, svc.gateway, svc.doc_kinds, work_id=work_id,
                         project_id=data.project_id, actor=p.actor))


@router.post("/works/{work_id}/card", status_code=201)
def paper_card_create(work_id: str, data: CardIn, request: Request, p: Principal = Depends(principal),
                      conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Per-paper note card: fields proposed as suggestions from cited answers on this work only."""
    return _work_card(request, conn, p, paper_card, work_id, data)


@router.post("/works/{work_id}/methods-checklist", status_code=201)
def methods_checklist_create(work_id: str, data: CardIn, request: Request, p: Principal = Depends(principal),
                             conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Methods checklist: cited items from this work only, plus the missing conditions a reproduction must settle."""
    return _work_card(request, conn, p, methods_checklist, work_id, data)


class PlanRef(BaseModel):
    plan_id: str
    revision: int = Field(ge=1)


class FeasibilityIn(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    dataset_ids: list[str] = Field(default_factory=list, max_length=10)
    template_id: str | None = None
    runs_planned: int | None = Field(default=None, ge=1, le=100000)
    deadline_days: int | None = Field(default=None, ge=1, le=3650)
    reference_plan: PlanRef | None = None  # comparable workload for the time estimate (review #68)


@router.post("/projects/{project_id}/feasibility", status_code=201)
def feasibility_create(project_id: str, data: FeasibilityIn, request: Request, p: Principal = Depends(principal),
                       conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Topic feasibility: cited literature answers + data/compute/time status from recorded facts (no score)."""
    svc = services(request)
    return _card(request, conn, "feasibility", project_id, {"project_id": project_id, **data.model_dump()},
                 partial(feasibility.create, conn, svc.gateway, svc.doc_kinds, svc.templates, gpu_info,
                         data.model_dump(), project_id=project_id, actor=p.actor))
