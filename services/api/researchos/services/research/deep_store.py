"""Persistence helpers for deep research runs."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from ...contracts.errors import not_found
from ...kernel.db import Row, jdump, jload, row, tx, utcnow
from ...kernel.events import emit


def get(conn: sqlite3.Connection, rid: str) -> Row:
    r = row(conn.execute("SELECT * FROM deep_research WHERE id=?", (rid,)))
    if r is None:
        raise not_found("deep research run")
    return {**r, "settings": jload(r["settings"]), "plan": jload(r["plan"]), "data": jload(r["data"]) or {}}


def save(conn: sqlite3.Connection, rid: str, actor: str, **fields: Any) -> Row:
    sets = {k: jdump(v) if k in ("plan", "data", "settings") else v for k, v in fields.items()}
    with tx(conn):
        conn.execute(f"UPDATE deep_research SET {', '.join(f'{k}=?' for k in sets)}, updated_at=? WHERE id=?",
                     (*sets.values(), utcnow(), rid))
        if "stage" in fields:
            emit(conn, f"deep_research.{fields['stage']}", actor=actor, entity_type="deep_research", entity_id=rid,
                 data={})
    return get(conn, rid)


def plan_hash(r: Row) -> str:
    return hashlib.sha256(jdump({"question": r["question"], "plan": r["plan"], "settings": r["settings"]})
                          .encode()).hexdigest()


def checkpoint_hash(conn: sqlite3.Connection, subject_id: str) -> str:
    """Approval resolver for `research_checkpoint`: subject id `<run id>:plan` binds question + plan + settings."""
    rid, _, what = subject_id.partition(":")
    if what != "plan":
        raise ValueError("unknown research checkpoint")
    return plan_hash(get(conn, rid))
