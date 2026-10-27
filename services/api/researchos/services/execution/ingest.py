"""After a run: hash outputs into artifacts, store headline metrics with their verification status, store health
signals. Recomputed values are authoritative; a failed recomputation marks the metric 'failed'."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from ...contracts.verification import HealthSignal
from ...kernel.db import jdump, new_id, tx, utcnow
from ..verification.recompute import RECOMPUTERS, base_name
from ..verification.view import DirRunView, finite

KINDS = {
    "run.json": "manifest",
    "metrics.jsonl": "metrics",
    "summary.json": "metrics",
    "stdout.log": "log",
    "stderr.log": "log",
    "access.log": "log",
}
TIMING_UNITS = {"ms", "s"}
FIGURE_SUFFIXES = (".png", ".jpg", ".jpeg")


def _kind(rel: str) -> str:
    if rel.startswith("predictions/"):
        return "predictions"
    if rel.startswith("checkpoints/"):
        return "model"
    if rel.lower().endswith(FIGURE_SUFFIXES):  # plots a template saves; slides/reports show them (ADR-165)
        return "figure"
    return KINDS.get(rel, "other")


def ingest(
    conn: sqlite3.Connection, data_dir: Path, run_id: str, view: DirRunView, signals: list[HealthSignal]
) -> list[dict[str, Any]]:
    run_dir = view.run_dir
    metrics = _metrics(view, signals)
    with tx(conn):
        for p in sorted(run_dir.rglob("*")):
            if not p.is_file() or "inputs" in p.relative_to(run_dir).parts or "home" in p.relative_to(run_dir).parts:
                continue
            rel = p.relative_to(run_dir).as_posix()
            conn.execute(
                "INSERT OR REPLACE INTO artifacts(id,run_id,path,sha256,size,kind,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    new_id("art"),
                    run_id,
                    p.relative_to(data_dir).as_posix(),
                    hashlib.sha256(p.read_bytes()).hexdigest(),
                    p.stat().st_size,
                    _kind(rel),
                    utcnow(),
                ),
            )
        for m in metrics:
            conn.execute(
                "INSERT OR REPLACE INTO run_metrics(run_id,name,split,value,unit,verification,reported_value) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, m["name"], m["split"], m["value"], m.get("unit"), m["verification"], m["reported_value"]),
            )
        for s in signals:
            conn.execute(
                "INSERT INTO health_signals(id,run_id,detector,severity,rule,value,selector,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (new_id("hs"), run_id, s.detector, s.severity, s.rule, jdump(s.value), jdump(s.selector), utcnow()),
            )
    return metrics


def _metrics(view: DirRunView, signals: list[HealthSignal]) -> list[dict[str, Any]]:
    failed = {
        s.value.get("metric") for s in signals if s.detector.startswith("metric_recompute") and s.severity == "fail"
    }
    suspended = any(s.detector.startswith("host_suspend") for s in signals)
    out = []
    for m in view.summary():
        reported = float(m["value"]) if finite(m.get("value")) else None
        ver, value = m.get("verification", "self_reported"), reported
        if ver == "recomputed":
            key = m["name"].rsplit(".", 1)[0] if "." in m["name"] else ""
            preds = view.predictions(f"{m['split']}__{key}" if key else m["split"])
            fn = RECOMPUTERS.get(base_name(m["name"]))
            if preds is not None and fn is not None:
                value = fn(preds)
                ver = "failed" if m["name"] in failed else "recomputed"
            else:
                ver = "unverified"
        if suspended and m.get("unit") in TIMING_UNITS:
            value, ver = None, "unverified"  # D06: timing measured across a host suspend is void
        out.append(
            {
                "name": m["name"],
                "split": m["split"],
                "value": value,
                "unit": m.get("unit"),
                "verification": ver,
                "reported_value": reported,
            }
        )
    return out
