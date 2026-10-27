"""Datasets, templates, immutable plans, runs, reports (K5 + K9 read side)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jload, row, rows
from ...services.content.report import export_run
from ...services.execution import advisor, compare, datasets, gates, plans, repro_bundle, run_figures, study, study_data
from ...services.execution import paired as paired_cmp
from ...services.identity.sessions import Principal
from ...services.orchestration import tasks as task_svc
from ..deps import cid, db, principal, services

router = APIRouter(prefix="/api/v1", tags=["experiments"])


class DatasetIn(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class PlanIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    project_id: str | None = None
    content: dict[str, Any]


class RevisionIn(BaseModel):
    content: dict[str, Any]


class RunIn(BaseModel):
    stage: str = Field(pattern="^(trial|confirm|final_eval|smoke)$")
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int = 0
    control: str | None = Field(default=None, pattern="^shuffled_labels$",
                                description="negative control: training labels shuffled by the worker (trial only)")


@router.post("/datasets", status_code=201)
def register_dataset(data: DatasetIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                     ) -> dict[str, Any]:
    return datasets.register(conn, Path(data.path), p.actor)


@router.get("/datasets")
def list_datasets(p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return rows(conn.execute("SELECT * FROM datasets ORDER BY created_at DESC"))


@router.get("/templates")
def list_templates(request: Request, p: Principal = Depends(principal)) -> list[dict[str, Any]]:
    return [{"id": t.manifest.id, "version": t.manifest.version, "description": t.manifest.description,
             "hash": t.hash, "status": t.status, "resource_class": t.manifest.resource_class,
             "params_schema": t.manifest.params_schema, "requires": list(t.manifest.requires)}
            for t in services(request).templates.all().values()]


@router.post("/plans", status_code=201)
def create_plan(data: PlanIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                ) -> dict[str, Any]:
    return plans.create(conn, title=data.title, content=data.content, actor=p.actor, project_id=data.project_id)


@router.post("/plans/{plan_id}/revisions", status_code=201)
def revise_plan(plan_id: str, data: RevisionIn, p: Principal = Depends(principal),
                conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return plans.revise(conn, plan_id, data.content, p.actor)


@router.get("/plans")
def list_plans(project_id: str | None = None, p: Principal = Depends(principal),
               conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return plans.list_plans(conn, project_id)


@router.get("/plans/{plan_id}/revisions/{revision}")
def get_revision(plan_id: str, revision: int, p: Principal = Depends(principal),
                 conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    r = plans.get_revision(conn, plan_id, revision)
    r["approved_by_revision"] = plans.approval_covers(conn, plan_id, revision)
    return r


@router.post("/plans/{plan_id}/revisions/{revision}/runs", status_code=202)
def start_run(plan_id: str, revision: int, data: RunIn, request: Request, p: Principal = Depends(principal),
              conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Fail fast on the same gates the worker re-checks at claim time (approval, final-eval once)."""
    rev = plans.get_revision(conn, plan_id, revision)
    g = gates.approval_gate(conn, rev, data.stage)
    if g:
        raise ApiError(409, g[0], g[1])
    key = request.headers.get("idempotency-key")
    if not key:
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key header required")
    if data.control and data.stage != "trial":
        raise ApiError(422, "bad_control", "a negative control runs at the trial stage only (never on the test set)")
    payload: dict[str, Any] = {"plan_id": plan_id, "revision": revision, "stage": data.stage, "params": data.params,
                               "seed": data.seed}
    if data.control:
        payload["control"] = data.control
    t, _ = task_svc.create(conn, services(request).kinds, kind="experiment.run", idempotency_key=key, actor=p.actor,
                           payload=payload, correlation_id=cid(request))
    return t


@router.get("/runs/{run_id}")
def get_run(run_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    r = row(conn.execute("SELECT r.*, t.state AS task_state, t.error_code, t.error_message FROM runs r "
                         "JOIN tasks t ON t.id=r.task_id WHERE r.id=?", (run_id,)))
    if r is None:
        raise not_found("run")
    r["metrics"] = rows(conn.execute("SELECT * FROM run_metrics WHERE run_id=?", (run_id,)))
    r["signals"] = [{**s, "value": jload(s["value"]), "selector": jload(s["selector"])} for s in
                    rows(conn.execute("SELECT * FROM health_signals WHERE run_id=? ORDER BY created_at", (run_id,)))]
    r["artifacts"] = rows(conn.execute("SELECT path, sha256, size, kind FROM artifacts WHERE run_id=?", (run_id,)))
    return r


@router.get("/tasks/{task_id}/runs")
def runs_of_task(task_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                 ) -> list[dict[str, Any]]:
    return rows(conn.execute("SELECT * FROM runs WHERE task_id=? ORDER BY attempt", (task_id,)))


@router.post("/runs/{run_id}/report")
def report(run_id: str, request: Request, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
           ) -> dict[str, Any]:
    return export_run(conn, services(request).settings.data_dir, run_id)


class StudyIn(BaseModel):
    plan_id: str
    revision: int
    protocol: dict[str, Any] = Field(description="objective (metric name), factors, chance, search/confirm settings")


@router.post("/studies", status_code=201)
def create_study(data: StudyIn, request: Request, p: Principal = Depends(principal),
                 conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    if "objective" not in data.protocol:
        raise ApiError(422, "bad_protocol", "protocol.objective (metric name) is required")
    return study.create(conn, services(request).kinds, plan_id=data.plan_id, revision=data.revision,
                        protocol=data.protocol, actor=p.actor)


@router.get("/studies/{study_id}")
def get_study(study_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
              ) -> dict[str, Any]:
    return study_data.get(conn, study_id)


@router.get("/studies/{study_id}/advice")
def study_advice(study_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                 ) -> dict[str, Any]:
    """Next-experiment advice (read-only): sensitivity, uncertainty, proposed batch, failure memory, stop rules."""
    return advisor.advise(conn, study_id)


@router.post("/studies/{study_id}/advance")
def advance_study(study_id: str, request: Request, p: Principal = Depends(principal),
                  conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    stage = study.advance(conn, services(request).kinds, study_id, p.actor)
    return {"stage": stage}


class CompareIn(BaseModel):
    items: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    paper_params: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


@router.post("/runs/{run_id}/compare")
def compare_run(run_id: str, data: CompareIn, p: Principal = Depends(principal),
                conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Compare freshly verified paper numbers to this run's independently recomputed metrics."""
    return compare.compare_run(conn, run_id, data.items, paper_params=data.paper_params)


class PairedIn(BaseModel):
    other_run_id: str
    split: str = Field(default="search_val", pattern="^(search_val|confirm_val|test)$")


@router.post("/runs/{run_id}/paired")
def paired(run_id: str, data: PairedIn, request: Request, p: Principal = Depends(principal),
           conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Run B (other) vs run A (this) on the same evaluation windows: McNemar, difference CI, Cohen's h, power."""
    svc = services(request)
    return paired_cmp.compare(conn, svc.settings.runs_dir, svc.tools, run_id, data.other_run_id, data.split)


@router.post("/plans/{plan_id}/bundle", status_code=201)
def plan_bundle(plan_id: str, request: Request, p: Principal = Depends(principal),
                conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Reproducible bundle of every run of the plan (failed ones included); download via /exports/{id}/files."""
    svc = services(request)
    return repro_bundle.build(conn, svc.settings.data_dir, svc.templates, plan_id, p.actor)


@router.get("/runs/{run_id}/figures/{name}", response_model=None)
def run_figure(run_id: str, name: str, request: Request, p: Principal = Depends(principal),
               conn: sqlite3.Connection = Depends(db)) -> FileResponse:
    """A figure of a run (template- or host-drawn image artifact), shown inline on the run page."""
    f = run_figures.figure_file(conn, services(request).settings.data_dir, run_id, name)
    return FileResponse(f, media_type="image/jpeg" if f.suffix.lower() in (".jpg", ".jpeg") else "image/png",
                        content_disposition_type="inline")
