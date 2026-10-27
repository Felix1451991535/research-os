"""citekey -> Work lookup. Lives in content (below literature and writing) so exporters, writing gates and
literature.citekeys share one resolver without an import cycle."""

from __future__ import annotations

import sqlite3
from typing import Any

from ...kernel.db import row


def lookup_work(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    """The one citekey -> Work resolver (citekeys table; legacy identifiers scheme 'citekey' as a fallback).
    Used by resolve(), the writing gates and exporters so they never disagree on what a key means."""
    return row(conn.execute(
        "SELECT w.* FROM citekeys k JOIN works w ON w.id=k.work_id WHERE k.citekey=? UNION ALL "
        "SELECT w.* FROM identifiers i JOIN works w ON w.id=i.work_id WHERE i.scheme='citekey' AND i.value=? LIMIT 1",
        (key, key)))
