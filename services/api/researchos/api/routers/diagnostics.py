"""Settings > About > "Export diagnostics" (ADR-230): the redacted, allow-listed zip as a download. Nothing is sent
anywhere; the owner attaches the file to a bug report after looking through it."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, Response

from ...services.identity.sessions import Principal
from ...services.ops.diagnostics import build_bundle
from ..deps import db, principal, services

router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])


@router.get("/bundle", response_class=Response)
def bundle(request: Request, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)) -> Response:
    name, body = build_bundle(services(request).settings, conn)
    return Response(body, media_type="application/zip",
                    headers={"content-disposition": f'attachment; filename="{name}"', "cache-control": "no-store"})
