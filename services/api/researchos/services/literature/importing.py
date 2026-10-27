"""Candidates -> library Works (metadata), optionally with an OA PDF.

Authorization: either a consumed K3 `knowledge_import` approval of exactly these records (manual selection), or the
owner's opt-in recorded on a saved search (`auto_import`), named in `authorized_by`. Dedupe is the writer's job
(hard identifiers only). The candidate keeps a link to its Work."""

from __future__ import annotations

import sqlite3
from typing import Any

from ...contracts.errors import ApiError
from ...kernel.db import tx, utcnow
from ...kernel.events import emit
from ..knowledge.writer import DbKnowledgeWriter
from . import candidates, oa
from .discovery import Ctx


def import_candidates(conn: sqlite3.Connection, ctx: Ctx, writer: DbKnowledgeWriter, cand_ids: list[str], *,
                      authorized_by: str, with_pdf: bool = False) -> list[dict[str, Any]]:
    if not authorized_by.startswith(("approval:", "saved_search:")):
        raise ApiError(403, "import_not_authorized", "imports need an approval or a saved-search opt-in")
    out = []
    for c in candidates.many(conn, cand_ids):
        rec = candidates.record_of(c)
        if not rec.title.strip():
            out.append({"candidate_id": c["id"], "status": "rejected", "reason": "no title"})
            continue
        wid, created = writer.upsert_work(rec, idempotency_key=f"cand:{c['id']}")
        with tx(conn):
            conn.execute("UPDATE lit_candidates SET work_id=?, updated_at=? WHERE id=?", (wid, utcnow(), c["id"]))
            emit(conn, "literature.candidate_imported", actor=ctx.actor, entity_type="work", entity_id=wid,
                 project_id=ctx.project_id, data={"candidate_id": c["id"], "authorized_by": authorized_by})
        item: dict[str, Any] = {"candidate_id": c["id"], "status": "created" if created else "reused", "work_id": wid}
        doi = dict(rec.identifiers).get("doi")
        if with_pdf and doi:
            loc, errors = oa.locate(conn, ctx, doi)
            item["pdf"] = oa.fetch_pdf(conn, ctx, writer, wid, loc) if loc else {"skipped": "no OA location found",
                                                                                  "errors": errors}
        out.append(item)
    return out
