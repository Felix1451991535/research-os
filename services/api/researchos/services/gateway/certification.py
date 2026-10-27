"""Certification-before-use (ADR-011): a (model id, prompt hash) pair may serve evidence-bearing tasks only after
an eval run passed thresholds, or an explicit, labelled owner override."""
from __future__ import annotations

import sqlite3
from typing import Any

from ...kernel.db import jdump, new_id, row, tx, utcnow

THRESHOLDS = {"locate_rate": 1.0, "support_precision": 0.90, "refusal_rate": 0.90}


def status(conn: sqlite3.Connection, model_id: str, prompt_hash: str) -> str:
    r = row(conn.execute("SELECT passed, override FROM eval_runs WHERE model_id=? AND prompt_hash=? "
                         "ORDER BY created_at DESC LIMIT 1", (model_id, prompt_hash)))
    if r is None:
        return "unevaluated"
    if r["override"]:
        return "override"
    return "certified" if r["passed"] else "failed"


def passes(metrics: dict[str, float]) -> bool:
    return all(metrics.get(k, 0.0) >= v for k, v in THRESHOLDS.items())


def record(conn: sqlite3.Connection, suite: str, model_id: str, prompt_hash: str, metrics: dict[str, Any],
           override: bool = False) -> str:
    rid = new_id("eval")
    with tx(conn):
        conn.execute("INSERT INTO eval_runs(id,suite,model_id,prompt_hash,metrics,passed,override,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (rid, suite, model_id, prompt_hash, jdump(metrics),
                                                  int(passes(metrics)), int(override), utcnow()))
    return rid
