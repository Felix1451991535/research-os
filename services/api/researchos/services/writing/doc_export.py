"""Authored doc -> gates -> exporter. Final exports are refused (no files written) when any gate error remains;
draft exports proceed with the diagnostics attached and are labelled draft."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal

from ...contracts.errors import ApiError
from ...contracts.exporters import Exporter
from ..content import docs
from ..content.export_host import publish
from . import doc_snapshot, gates

EXPORTABLE = ("report", "manuscript", "note", "paper_card", "methods_checklist", "feasibility")


def check(conn: sqlite3.Connection, doc_id: str, mode: gates.Mode) -> dict[str, Any]:
    d = docs.get(conn, doc_id)
    res = gates.check(conn, doc_snapshot.parse(d["body"]), mode)
    return {"doc_id": doc_id, "version": d["version"], "mode": mode, "ok": not res.errors, "counts": res.counts,
            "diagnostics": [x.__dict__ for x in res.diagnostics]}


def export(conn: sqlite3.Connection, data_dir: Path, exporter: Exporter, doc_id: str,
           mode: Literal["draft", "final"], options: dict[str, Any], actor: str, *,
           export_id: str | None = None) -> dict[str, Any]:
    d = docs.get(conn, doc_id)
    if d["kind"] not in EXPORTABLE:
        raise ApiError(422, "unsupported_doc_kind", f"{d['kind']} docs are not exportable")
    blocks = doc_snapshot.parse(d["body"])
    res = gates.check(conn, blocks, mode)
    if mode == "final" and res.errors:
        raise ApiError(409, "export_gates_failed", f"{len(res.errors)} citation/number problem(s); fix them or "
                       "export as draft", details={"diagnostics": [x.__dict__ for x in res.diagnostics],
                                                  "counts": res.counts})
    kind = "report" if d["kind"] in ("note", "paper_card", "methods_checklist", "feasibility") else d["kind"]
    snap = doc_snapshot.to_snapshot(doc_id, int(d["version"]), d["title"] or "Untitled", kind, blocks,
                                    res.evidence_cite)
    out = publish(conn, data_dir, exporter, snap, mode, options, actor, export_id=export_id,
                  project_id=d["project_id"])
    return {**out, "gates": {"counts": res.counts, "diagnostics": [x.__dict__ for x in res.diagnostics]}}
