"""Canary sweep (ARCHITECTURE §6.7): prepare (dataset + 2 plans) -> owner approves plans -> start -> status."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request

from ...services.execution import canary
from ...services.identity.sessions import Principal
from ..deps import db, principal, services

router = APIRouter(prefix="/api/v1/canary", tags=["experiments"])


@router.post("", status_code=201)
def prepare(request: Request, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
            ) -> dict[str, Any]:
    svc = services(request)
    return canary.prepare(conn, svc.settings.data_dir, svc.templates, p.actor)


@router.post("/{canary_id}/start", status_code=202)
def start(canary_id: str, request: Request, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
          ) -> dict[str, Any]:
    return canary.start(conn, services(request).kinds, canary_id, p.actor)


@router.get("/{canary_id}")
def status(canary_id: str, p: Principal = Depends(principal), conn: sqlite3.Connection = Depends(db)
           ) -> dict[str, Any]:
    return canary.status(conn, canary_id)
