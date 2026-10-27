"""M5 literature agent over HTTP: federated search, citation chaining, approved imports, retraction checks, OA,
literature settings, alerts."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jload, row, rows, tx, utcnow
from ...services.identity.sessions import Principal
from ...services.knowledge import imports
from ...services.knowledge.writer import DbKnowledgeWriter
from ...services.literature import candidates, discovery, importing, oa, retraction
from ..deps import db, principal, services

router = APIRouter(prefix="/api/v1", tags=["literature"])


class SearchIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    sources: list[str] = Field(min_length=1, max_length=10)
    filters: dict[str, str] = Field(default_factory=dict)
    page_size: int = Field(default=25, ge=1, le=100)
    review_first: bool = False
    project_id: str | None = None


class OrientationIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    sources: list[str] = Field(min_length=1, max_length=10)
    page_size: int = Field(default=20, ge=1, le=50)
    seeds: int = Field(default=5, ge=1, le=10)
    project_id: str | None = None


class ChainIn(BaseModel):
    candidate_id: str
    direction: str = Field(pattern="^(references|cited_by)$")
    sources: list[str] | None = None
    page_size: int = Field(default=25, ge=1, le=100)


class SelectionIn(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=500)
    with_pdf: bool = False
    project_id: str | None = None


class RetractionIn(BaseModel):
    work_ids: list[str] | None = None


class LitSettingsIn(BaseModel):
    pdf_download: bool | None = None
    retraction_checks: bool | None = None


def _ctx(request: Request, p: Principal, project_id: str | None = None) -> discovery.Ctx:
    svc = services(request)
    return discovery.Ctx(svc.extensions, svc.keystore, p.actor, project_id)


@router.post("/literature/search")
def search(data: SearchIn, request: Request, p: Principal = Depends(principal),
           conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return discovery.search(conn, _ctx(request, p, data.project_id), data.text, data.sources, filters=data.filters,
                            page_size=data.page_size, review_first=data.review_first)


@router.post("/literature/orientation")
def orientation(data: OrientationIn, request: Request, p: Principal = Depends(principal),
                conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Field orientation: reviews first, 1-hop citation expansion, overview map (counts from logged runs)."""
    return discovery.orientation(conn, _ctx(request, p, data.project_id), data.text, data.sources,
                                 page_size=data.page_size, seeds=data.seeds)


@router.post("/literature/chain")
def chain(data: ChainIn, request: Request, p: Principal = Depends(principal),
          conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    rec = candidates.record_of(candidates.get(conn, data.candidate_id))
    return discovery.chain(conn, _ctx(request, p), rec, data.direction, data.sources, data.page_size)


@router.get("/literature/candidates/{candidate_id}")
def get_candidate(candidate_id: str, p: Principal = Depends(principal),
                  conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return candidates.get(conn, candidate_id)


@router.get("/literature/runs/{run_id}")
def get_run(run_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    r = row(conn.execute("SELECT * FROM lit_search_runs WHERE id=?", (run_id,)))
    if r is None:
        raise not_found("search run")
    return {**r, **{k: jload(r[k]) for k in ("query", "sources", "per_source", "candidate_ids", "new_candidate_ids")}}


@router.post("/literature/import/selection-hash")
def selection_hash(data: SelectionIn, p: Principal = Depends(principal),
                   conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """The knowledge_import approval subject id for exactly these candidates (approve it, then import)."""
    recs = [candidates.record_of(c) for c in candidates.many(conn, data.candidate_ids)]
    return {"subject_type": "knowledge_import", "subject_id": imports.records_hash(recs), "records": len(recs)}


@router.post("/literature/import")
def import_selection(data: SelectionIn, request: Request, p: Principal = Depends(principal),
                     conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    svc = services(request)
    cands = candidates.many(conn, data.candidate_ids)
    if len(cands) != len(set(data.candidate_ids)):
        raise ApiError(404, "not_found", "unknown candidate id in selection")
    h = imports.consume_approval(conn, [candidates.record_of(c) for c in cands], p.actor)
    writer = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.literature", actor=p.actor,
                               project_id=data.project_id, max_bytes=svc.settings.max_upload_mb * 1024 * 1024)
    res = importing.import_candidates(conn, _ctx(request, p, data.project_id), writer, data.candidate_ids,
                                      authorized_by=f"approval:{h}", with_pdf=data.with_pdf)
    return {"results": res}


@router.post("/literature/retractions/check")
def check_retractions(data: RetractionIn, request: Request, p: Principal = Depends(principal),
                      conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return retraction.check_works(conn, _ctx(request, p), data.work_ids)


@router.get("/literature/oa")
def oa_location(request: Request, doi: str = Query(min_length=3, max_length=300), p: Principal = Depends(principal),
                conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    loc, errors = oa.locate(conn, _ctx(request, p), doi)
    return {"location": loc.__dict__ if loc else None, "errors": errors}


@router.get("/literature/settings")
def get_lit_settings(p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return oa.lit_settings(conn)


@router.put("/literature/settings")
def put_lit_settings(data: LitSettingsIn, p: Principal = Depends(principal),
                     conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return oa.set_lit_settings(conn, data.model_dump(exclude_none=True), p.actor)


@router.get("/alerts")
def list_alerts(unread: bool = False, limit: int = Query(100, ge=1, le=500), p: Principal = Depends(principal),
                conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    q = "SELECT * FROM alerts" + (" WHERE read_at IS NULL" if unread else "") + " ORDER BY created_at DESC LIMIT ?"
    return [{**a, "data": jload(a["data"])} for a in rows(conn.execute(q, (limit,)))]


@router.post("/alerts/{alert_id}/read")
def read_alert(alert_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
               ) -> dict[str, Any]:
    with tx(conn):
        n = conn.execute("UPDATE alerts SET read_at=? WHERE id=? AND read_at IS NULL", (utcnow(), alert_id)).rowcount
    return {"updated": n}
