"""Library: citekeys bound to evidence, bibliography export (BibTeX/RIS/CSL-JSON), tags/collections, backlinks,
global search, file imports (BibTeX/RIS/CSL-JSON/Zotero RDF via FileImporters) into the candidate list, and the
owner's review of title-based identification suggestions."""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from ...contracts.errors import ApiError
from ...contracts.importers import FileImportRequest
from ...services.identity.sessions import Principal
from ...services.knowledge.writer import DbKnowledgeWriter
from ...services.literature import bibexport, citekeys, discovery, identify_title, library_index, weight
from ..deps import db, principal, services

router = APIRouter(prefix="/api/v1", tags=["library"])
MAX_FILE = 20_000_000


class LabelsIn(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=100)


class ExportIn(BaseModel):
    format: str = Field(pattern="^(bibtex|ris|csl-json)$")
    work_ids: list[str] | None = None


@router.get("/library/works")
def works(tag: str | None = None, collection: str | None = None, retracted: bool | None = None,
          limit: int = Query(200, ge=1, le=1000), p: Principal = Depends(principal),
          conn: sqlite3.Connection = Depends(db)) -> list[dict[str, Any]]:
    return library_index.list_works(conn, tag=tag, collection=collection, retracted=retracted, limit=limit)


@router.put("/works/{work_id}/tags")
def set_tags(work_id: str, data: LabelsIn, p: Principal = Depends(principal),
             conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return {"tags": library_index.set_labels(conn, work_id, "tag", data.names, p.actor)}


@router.put("/works/{work_id}/collections")
def set_collections(work_id: str, data: LabelsIn, p: Principal = Depends(principal),
                    conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return {"collections": library_index.set_labels(conn, work_id, "collection", data.names, p.actor)}


@router.get("/works/{work_id}/backlinks")
def backlinks(work_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
              ) -> list[dict[str, Any]]:
    return library_index.backlinks(conn, work_id)


@router.post("/works/{work_id}/citekey")
def assign_citekey(work_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                   ) -> dict[str, Any]:
    return {"citekey": citekeys.assign(conn, work_id, p.actor)}


@router.get("/citekeys/{key}")
def resolve_citekey(key: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                    ) -> dict[str, Any]:
    return citekeys.resolve(conn, key)


@router.post("/library/export")
def export(data: ExportIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> Response:
    fn, mime, ext = bibexport.FORMATS[data.format]
    body = fn(conn, data.work_ids)
    return Response(body, media_type=f"{mime}; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="library.{ext}"'})


@router.get("/search/global")
def global_search(q: str = Query(min_length=2, max_length=300), project_id: str | None = None,
                  p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    return library_index.global_search(conn, q, project_id)


@router.post("/literature/files/{importer}")
async def import_file(importer: str, request: Request, file: UploadFile = File(...),
                      p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Parse a bibliography export into candidates (logged as a run); importing into the library still needs the
    usual approval of the selection."""
    svc = services(request)
    fi = svc.extensions.file_importers.get(importer)
    if fi is None:
        raise ApiError(404, "unknown_importer", f"no file importer {importer!r}",
                       details={"available": sorted(svc.extensions.file_importers)})
    data = await file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ApiError(413, "file_too_large", f"limit {MAX_FILE} bytes")
    try:
        recs = fi.parse_file(FileImportRequest(file.filename or "upload", data))
    except (ValueError, UnicodeDecodeError) as e:
        raise ApiError(422, "unparseable_file", f"{importer}: {e}") from None
    return discovery.record_file_run(conn, discovery.Ctx(svc.extensions, svc.keystore, p.actor), importer,
                                     file.filename or "upload", hashlib.sha256(data).hexdigest(), recs)


class TiersIn(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=1900, le=2100)
    entries: list[dict[str, str]] = Field(min_length=1, max_length=50_000)


@router.put("/library/venue-tiers")
def import_tiers(data: TiersIn, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                 ) -> dict[str, Any]:
    """Owner-imported venue tier table (JCR / CAS etc.); tiers are never guessed."""
    return weight.set_tiers(conn, source=data.source, year=data.year, entries=data.entries, actor=p.actor)


@router.get("/works/{work_id}/weight")
def work_weight(work_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                ) -> dict[str, Any]:
    return weight.weight(conn, work_id)


# ---- title-based identification: suggestions the owner confirms or rejects (ADR-233) ----

@router.get("/library/identifications")
def identifications(p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
                    ) -> list[dict[str, Any]]:
    return identify_title.pending(conn)


@router.post("/library/identifications/{ident_id}/confirm")
def confirm_identification(ident_id: str, request: Request, p: Principal = Depends(principal),
                           conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    svc = services(request)
    writer = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.identify", actor=p.actor, project_id=None,
                               max_bytes=svc.settings.max_upload_mb * 1024 * 1024)
    return {"work_id": identify_title.confirm(conn, writer, ident_id, svc.settings.data_dir)}


@router.post("/library/identifications/{ident_id}/reject", status_code=204)
def reject_identification(ident_id: str, p: Principal = Depends(principal),
                          conn: sqlite3.Connection = Depends(db)) -> None:
    identify_title.reject(conn, ident_id, p.actor)
