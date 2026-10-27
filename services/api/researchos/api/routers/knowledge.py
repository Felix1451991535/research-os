"""Library (documents), search, evidence (K7)."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...services.identity.sessions import Principal
from ...services.knowledge import evidence as ev
from ...services.knowledge import library, reading
from ...services.knowledge.importer import enqueue_parse, import_document
from ...services.knowledge.retrieval import FtsRetriever
from ..deps import cid, db, principal, services

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


class ImportResult(BaseModel):
    version_id: str
    created: bool
    parse_state: str
    task_id: str | None


class Hit(BaseModel):
    chunk_id: str
    version_id: str
    work_id: str
    title: str
    page_index: int
    page_label: str | None
    char_start: int
    char_end: int
    text: str
    score: float
    section_role: str | None
    quality: str


class EvidenceIn(BaseModel):
    version_id: str
    page_index: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=5000)
    kind: str = "source_fact"
    extracted_value: str | None = Field(default=None, max_length=2000)
    project_id: str | None = None


class EvidenceOut(BaseModel):
    id: str
    version_id: str
    page_index: int
    char_start: int
    char_end: int
    quote: str
    kind: str
    trust: str
    created_at: str


class VerifyOut(BaseModel):
    status: str
    detail: str


@router.post("/documents/upload", response_model=ImportResult, status_code=201)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    p: Principal = Depends(principal),
    conn: sqlite3.Connection = Depends(db),
) -> ImportResult:
    """Content-addressed import: re-uploading the same bytes returns the existing version (no duplicate)."""
    svc = services(request)
    data = await file.read(svc.settings.max_upload_mb * 1024 * 1024 + 1)
    res = import_document(
        conn,
        svc.blobs,
        svc.kinds,
        data=data,
        filename=file.filename or "upload",
        project_id=project_id,
        actor=p.actor,
        max_bytes=svc.settings.max_upload_mb * 1024 * 1024,
        cid=cid(request),
    )
    v = res["version"]
    return ImportResult(
        version_id=v["version_id"], created=res["created"], parse_state=v["parse_state"], task_id=res["task_id"]
    )


@router.get("/documents")
def list_documents(
    project_id: str | None = None, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> list[dict[str, Any]]:
    return library.list_documents(conn, project_id)


@router.get("/documents/{version_id}")
def get_document(
    version_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    return library.get_document(conn, version_id)


@router.get("/documents/{version_id}/pages/{page_index}")
def get_page(
    version_id: str, page_index: int, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    return library.get_page(conn, version_id, page_index)


@router.get("/documents/{version_id}/file", response_model=None)
def original_file(
    version_id: str, request: Request, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> FileResponse:
    d = library.get_document(conn, version_id)
    path = services(request).settings.data_dir / str(
        conn.execute("SELECT stored_path FROM document_versions WHERE version_id=?", (version_id,)).fetchone()[0]
    )
    return FileResponse(path, media_type=d["mime"], filename=d["original_name"], content_disposition_type="inline")


@router.get("/search", response_model=list[Hit])
def search(
    q: str = Query(min_length=1, max_length=500),
    project_id: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    p: Principal = Depends(principal),
    conn: sqlite3.Connection = Depends(db),
) -> list[Any]:
    return [h.__dict__ for h in FtsRetriever(conn).search(q, project_id=project_id, limit=limit)]


@router.post("/evidence", response_model=EvidenceOut, status_code=201)
def create_evidence(
    data: EvidenceIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> dict[str, Any]:
    return ev.create(
        conn,
        version_id=data.version_id,
        page_index=data.page_index,
        quote=data.quote,
        created_by=p.actor,
        project_id=data.project_id,
        kind=data.kind,
        extracted_value=data.extracted_value,
    )


@router.get("/evidence/{evidence_id}/verify", response_model=VerifyOut)
def verify_evidence(
    evidence_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
) -> VerifyOut:
    v = ev.verify(conn, evidence_id)
    return VerifyOut(status=v.status, detail=v.detail)


@router.post("/documents/{version_id}/reparse", status_code=202)
def reparse(version_id: str, request: Request, p: Principal = Depends(principal),
            conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Re-parse with the current parser if the document was parsed by an older one (reading aids then run on the
    new chunks); otherwise returns the existing parse task. Idempotent per parser version."""
    v = library.get_document(conn, version_id)
    t = enqueue_parse(conn, services(request).kinds, version_id, actor=p.actor, project_id=v.get("project_id"),
                      cid=cid(request))
    return {"task_id": t["id"], "parser_version": v.get("parser_version")}


@router.post("/documents/{version_id}/reading")
def extract_reading(version_id: str, project_id: str | None = None, p: Principal = Depends(principal),
                    conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Captions first, then author main-point sentences, stored as quote-verified Evidence (idempotent; no model)."""
    library.get_document(conn, version_id)
    return {"new": reading.extract(conn, version_id, project_id), **reading.listing(conn, version_id)}


@router.get("/documents/{version_id}/reading")
def get_reading(version_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                ) -> dict[str, Any]:
    return reading.listing(conn, version_id)
