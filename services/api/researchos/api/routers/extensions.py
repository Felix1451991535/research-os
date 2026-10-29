"""Plugin extension points over HTTP: external search, approved imports, file importers, exports, dataset builds."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ...contracts.datasources import SearchQuery
from ...contracts.errors import ApiError
from ...contracts.http import HttpDenied, HttpUnavailable
from ...contracts.importers import FileImportRequest
from ...services import plugin_settings
from ...services.content.export_host import export_download, language_of, publish, run_project, snapshot_from_run
from ...services.identity.sessions import Principal
from ...services.knowledge import imports
from ...services.knowledge.http_fetch import HostHttp
from ...services.knowledge.writer import DbKnowledgeWriter
from ..deps import db, principal, services

router = APIRouter(prefix="/api/v1", tags=["extensions"])


class SearchIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    filters: dict[str, str] = Field(default_factory=dict)
    page_size: int = Field(default=25, ge=1, le=100)
    cursor: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class ImportIn(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    project_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class ExportIn(BaseModel):
    run_id: str
    mode: str = Field(default="final", pattern="^(draft|final)$")
    options: dict[str, Any] = Field(default_factory=dict)


@router.get("/extensions")
def list_extensions(request: Request, p: Principal = Depends(principal)) -> dict[str, Any]:
    ext = services(request).extensions
    return {**ext.summary(), "rejected": ext.rejected}


@router.post("/sources/{name}/search")
def external_search(name: str, data: SearchIn, request: Request, p: Principal = Depends(principal),
                    conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    svc = services(request)
    ext = svc.extensions
    src = ext.sources.get(name)
    if src is None:
        raise ApiError(404, "unknown_source", f"no bibliographic source {name!r}")
    http = HostHttp(ext.granted_hosts(f"datasources:{name}", src.spec.hosts))
    eff = plugin_settings.effective(conn, svc.keystore, ext, "datasources", name, data.settings)
    try:
        page = src.search(SearchQuery(data.text, data.filters, data.page_size, data.cursor), http, eff)
    except HttpDenied as e:
        raise ApiError(403, "network_denied", str(e)) from e
    except HttpUnavailable as e:
        raise ApiError(503, "source_unavailable", str(e), retryable=True) from e
    recs = [asdict(r) for r in page.records]
    return {
        "records": recs,
        "next_cursor": page.next_cursor,
        "total": page.total,
        "warnings": list(page.warnings),
        "selection_hash_of_all": imports.records_hash(list(page.records)),
    }


@router.post("/imports/{importer}")
def run_import(
    importer: str,
    data: ImportIn,
    request: Request,
    p: Principal = Depends(principal),
    conn: sqlite3.Connection = Depends(db),
) -> dict[str, Any]:
    svc = services(request)
    imp = svc.extensions.importers.get(importer)
    if imp is None:
        raise ApiError(404, "unknown_importer", f"no importer {importer!r}")
    records = [imports.record_from_dict(r) for r in data.records]
    eff = plugin_settings.effective(conn, svc.keystore, svc.extensions, "importers", importer, data.settings)
    imports.consume_approval(conn, records, p.actor)
    writer = DbKnowledgeWriter(
        conn,
        svc.blobs,
        svc.kinds,
        plugin=importer,
        actor=p.actor,
        project_id=data.project_id,
        max_bytes=svc.settings.max_upload_mb * 1024 * 1024,
    )
    http = HostHttp(svc.extensions.granted_hosts(f"importers:{importer}", imp.spec.hosts))
    results = imp.import_records(tuple(records), writer, http, eff)
    return {"results": [asdict(r) for r in results]}


@router.post("/imports/{importer}/parse-file")
async def parse_file(
    importer: str, request: Request, file: UploadFile = File(...), p: Principal = Depends(principal)
) -> dict[str, Any]:
    fi = services(request).extensions.file_importers.get(importer)
    if fi is None:
        raise ApiError(404, "unknown_importer", f"no file importer {importer!r}")
    recs = fi.parse_file(FileImportRequest(file.filename or "upload", await file.read(10_000_000)))
    return {"records": [asdict(r) for r in recs], "selection_hash_of_all": imports.records_hash(recs)}


@router.post("/exports/{exporter}")
def export(
    exporter: str,
    data: ExportIn,
    request: Request,
    p: Principal = Depends(principal),
    conn: sqlite3.Connection = Depends(db),
) -> dict[str, Any]:
    svc = services(request)
    exp = svc.extensions.exporters.get(exporter)
    if exp is None:
        raise ApiError(404, "unknown_exporter", f"no exporter {exporter!r}")
    snap = snapshot_from_run(conn, svc.settings.data_dir, data.run_id, language_of(data.options))
    return publish(conn, svc.settings.data_dir, exp, snap, data.mode, data.options, p.actor,  # type: ignore[arg-type]
                   project_id=run_project(conn, data.run_id))


@router.get("/exports/{export_id}/files/{name}", response_model=None)
def download_export(export_id: str, name: str, request: Request, p: Principal = Depends(principal),
                    conn: sqlite3.Connection = Depends(db)) -> FileResponse:
    """A file of a registered export (e.g. a .pptx for the iPad). Single-owner host: every principal is one of the
    owner's paired devices, so the record check is the boundary (ADR-172)."""
    f = export_download(conn, services(request).settings.data_dir, export_id, name)
    return FileResponse(f, filename=f.name, content_disposition_type="attachment")


class SettingsIn(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/extensions/{group}/{name}/settings")
def get_settings(group: str, name: str, request: Request, p: Principal = Depends(principal),
                 conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    """Schema, non-secret values, and for secret fields only whether they are set (never their value)."""
    svc = services(request)
    return plugin_settings.view(conn, svc.keystore, svc.extensions, group, name)


@router.put("/extensions/{group}/{name}/settings")
def put_settings(group: str, name: str, data: SettingsIn, request: Request, p: Principal = Depends(principal),
                 conn: sqlite3.Connection = Depends(db)) -> dict[str, Any]:
    svc = services(request)
    return plugin_settings.update(conn, svc.keystore, svc.extensions, group, name, data.values, p.actor)
