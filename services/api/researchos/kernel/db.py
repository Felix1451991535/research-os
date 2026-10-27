"""K1 SQLite access: WAL, foreign keys on, explicit IMMEDIATE write transactions.

Only the API process and the worker (same host, local disk) open the DB file (baseline §3).
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Row = dict[str, Any]


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection, *, join: bool = False) -> Iterator[sqlite3.Connection]:
    """Write transaction. BEGIN IMMEDIATE takes the write lock up front so check-then-write is atomic.
    Nested use requires explicit join=True; the outer caller owns commit and rollback."""
    if join and conn.in_transaction:
        yield conn
        return
    for attempt in range(100):
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as e:  # pragma: no cover - contention beyond busy_timeout
            if "locked" not in str(e) or attempt == 99:
                raise
            time.sleep(0.05)
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def rows(cur: sqlite3.Cursor) -> list[Row]:
    return [dict(r) for r in cur.fetchall()]


def row(cur: sqlite3.Cursor) -> Row | None:
    r = cur.fetchone()
    return dict(r) if r else None


def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def jload(s: str | None) -> Any:
    return json.loads(s) if s else None
