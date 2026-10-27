"""Worker registry + heartbeat (own table, not settings)."""
from __future__ import annotations

import socket
import sqlite3
from datetime import UTC, datetime

from ...kernel.db import Row, new_id, row, tx, utcnow
from ...kernel.events import emit


def register(conn: sqlite3.Connection, pid: int, version: str) -> str:
    wid = new_id("wrk")
    with tx(conn):
        conn.execute("INSERT INTO workers(id,host,pid,version,started_at,heartbeat_at) VALUES (?,?,?,?,?,?)",
                     (wid, socket.gethostname(), pid, version, utcnow(), utcnow()))
        emit(conn, "worker.started", actor=f"worker:{wid}", entity_type="worker", entity_id=wid, data={"pid": pid})
    return wid


def heartbeat(conn: sqlite3.Connection, wid: str) -> None:
    with tx(conn):
        conn.execute("UPDATE workers SET heartbeat_at=? WHERE id=?", (utcnow(), wid))


def stop(conn: sqlite3.Connection, wid: str) -> None:
    with tx(conn):
        conn.execute("UPDATE workers SET stopped_at=? WHERE id=?", (utcnow(), wid))
        emit(conn, "worker.stopped", actor=f"worker:{wid}", entity_type="worker", entity_id=wid)


def status(conn: sqlite3.Connection, stale_s: float) -> dict[str, object]:
    r: Row | None = row(conn.execute(
        "SELECT * FROM workers WHERE stopped_at IS NULL ORDER BY heartbeat_at DESC LIMIT 1"))
    if not r:
        return {"alive": False, "last_heartbeat": None}
    age = (datetime.now(UTC) - datetime.fromisoformat(r["heartbeat_at"])).total_seconds()
    return {"alive": age < stale_s, "worker_id": r["id"], "pid": r["pid"], "last_heartbeat": r["heartbeat_at"],
            "age_s": round(age, 1)}
