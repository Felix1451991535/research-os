"""Idempotency records (one logical write per user intent). TTL cleanup keeps the table bounded."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..contracts.errors import ApiError
from .db import jdump, row, utcnow


def request_hash(payload: Any) -> str:
    return hashlib.sha256(jdump(payload).encode()).hexdigest()


def lookup(conn: sqlite3.Connection, scope: str, key: str, req_hash: str) -> tuple[int, Any] | None:
    r = row(conn.execute("SELECT * FROM idempotency WHERE scope=? AND key=?", (scope, key)))
    if not r:
        return None
    if r["request_hash"] != req_hash:
        raise ApiError(422, "idempotency_mismatch", "Idempotency-Key reused with a different request body")
    return int(r["status_code"]), json.loads(r["response_json"])


def store(conn: sqlite3.Connection, scope: str, key: str, req_hash: str, status: int, response: Any) -> None:
    """Call inside the write transaction that performed the action."""
    conn.execute("INSERT INTO idempotency(key,scope,request_hash,status_code,response_json,created_at) "
                 "VALUES (?,?,?,?,?,?)", (key, scope, req_hash, status, jdump(response), utcnow()))


def purge_expired(conn: sqlite3.Connection, ttl_days: int) -> int:
    cutoff = (datetime.now(UTC) - timedelta(days=ttl_days)).isoformat(timespec="milliseconds")
    return conn.execute("DELETE FROM idempotency WHERE created_at < ? AND status_code != 102", (cutoff,)).rowcount


def checkpoint(conn: sqlite3.Connection, scope: str, key: str, progress: Any) -> None:
    """Persist a resumable operation step in the same transaction as its domain writes."""
    conn.execute("UPDATE idempotency SET response_json=? WHERE scope=? AND key=? AND status_code=102",
                 (jdump(progress), scope, key))


@contextmanager
def operation_lock(path: Path) -> Iterator[None]:
    """Host-local OS lock: released on process death; lock files must never be unlinked while in use."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ApiError(409, "operation_in_progress", "This operation is running; retry the same key.") from exc
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
