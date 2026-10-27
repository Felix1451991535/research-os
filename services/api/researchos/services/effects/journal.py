"""Append-only effect journal helpers + plan state transitions."""

from __future__ import annotations

import sqlite3
from typing import Any

from ...kernel.db import jdump, jload, rows, tx, utcnow
from ...kernel.events import emit


def write(conn: sqlite3.Connection, pid: str, i: int, phase: str, actor: str, detail: dict[str, Any] | None = None
          ) -> None:
    with tx(conn):  # committed before/after each filesystem action: the journal is the recovery record
        conn.execute("INSERT INTO effect_journal(plan_id,op_index,phase,detail,actor,at) VALUES (?,?,?,?,?,?)",
                     (pid, i, phase, jdump(detail or {}), actor, utcnow()))


def last(conn: sqlite3.Connection, pid: str) -> dict[int, dict[str, Any]]:
    """Latest journal row per op."""
    out: dict[int, dict[str, Any]] = {}
    for r in rows(conn.execute("SELECT op_index, phase, detail FROM effect_journal WHERE plan_id=? ORDER BY seq",
                               (pid,))):
        out[r["op_index"]] = {"phase": r["phase"], "detail": jload(r["detail"])}
    return out


def entries(conn: sqlite3.Connection, pid: str) -> list[dict[str, Any]]:
    return [{**r, "detail": jload(r["detail"])} for r in rows(conn.execute(
        "SELECT seq, op_index, phase, detail, actor, at FROM effect_journal WHERE plan_id=? ORDER BY seq", (pid,)))]


def set_state(conn: sqlite3.Connection, pid: str, state: str, actor: str, reason: str | None = None) -> None:
    with tx(conn):
        conn.execute("UPDATE effect_plans SET state=?, blocked_reason=?, updated_at=? WHERE id=?",
                     (state, reason, utcnow(), pid))
        emit(conn, f"effect.{state}", actor=actor, entity_type="effect_plan", entity_id=pid,
             data={"reason": reason} if reason else {})
