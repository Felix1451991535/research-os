"""K1 append-only event log + provenance index + durable consumer cursors.

Every state change writes its event in the SAME transaction as the change (callers pass the open connection).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .db import Row, jdump, jload, rows, utcnow


def emit(conn: sqlite3.Connection, kind: str, *, actor: str = "system", entity_type: str | None = None,
         entity_id: str | None = None, project_id: str | None = None, correlation_id: str | None = None,
         data: dict[str, Any] | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO events(ts,actor,kind,entity_type,entity_id,project_id,correlation_id,data) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (utcnow(), actor, kind, entity_type, entity_id, project_id, correlation_id, jdump(data or {})))
    return int(cur.lastrowid or 0)


def prov_link(conn: sqlite3.Connection, subject: str, relation: str, obj: str, event_seq: int | None = None) -> None:
    """Provenance index row (wasDerivedFrom / wasGeneratedBy / attributedTo). Events stay the source of truth."""
    conn.execute("INSERT OR IGNORE INTO prov_links(subject,relation,object,event_seq,created_at) VALUES (?,?,?,?,?)",
                 (subject, relation, obj, event_seq, utcnow()))


def list_events(conn: sqlite3.Connection, after_seq: int = 0, limit: int = 200, project_id: str | None = None,
                entity_id: str | None = None) -> list[Row]:
    sql = "SELECT * FROM events WHERE seq>?"
    args: list[object] = [after_seq]
    if project_id:
        sql += " AND project_id=?"
        args.append(project_id)
    if entity_id:
        sql += " AND entity_id=?"
        args.append(entity_id)
    sql += " ORDER BY seq LIMIT ?"
    args.append(limit)
    out = rows(conn.execute(sql, args))
    for e in out:
        e["data"] = jload(e["data"]) or {}
    return out


def consumer_position(conn: sqlite3.Connection, consumer: str) -> int:
    r = conn.execute("SELECT last_seq FROM event_cursors WHERE consumer=?", (consumer,)).fetchone()
    return int(r[0]) if r else 0


def consumer_advance(conn: sqlite3.Connection, consumer: str, seq: int) -> None:
    """Call inside the same transaction as the consumer's side effects (at-least-once + idempotent handlers)."""
    conn.execute("INSERT INTO event_cursors(consumer,last_seq,updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(consumer) DO UPDATE SET last_seq=excluded.last_seq, updated_at=excluded.updated_at "
                 "WHERE excluded.last_seq > event_cursors.last_seq", (consumer, seq, utcnow()))
