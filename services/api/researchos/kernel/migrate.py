"""Numbered migrations: core/NNNN_*.sql plus plugin namespaces plugins/<ns>/NNNN_*.sql.

Only the API process / CLI migrates (single migration owner). The worker calls `pending()` and refuses to start
if anything is pending. A non-empty DB is snapshotted before migrating. No downgrades: restore from backup.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .db import tx, utcnow

CORE_DIR = Path(__file__).parent / "migrations" / "core"
CORE_NS = "core"


class PendingMigrations(RuntimeError):
    pass


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (namespace TEXT NOT NULL, version INTEGER NOT NULL, "
                 "name TEXT NOT NULL, applied_at TEXT NOT NULL, PRIMARY KEY (namespace, version))")


def discover(directory: Path) -> list[tuple[int, Path]]:
    return sorted((int(p.name.split("_", 1)[0]), p) for p in directory.glob("*.sql"))


def current_version(conn: sqlite3.Connection, namespace: str = CORE_NS) -> int:
    _ensure_table(conn)
    r = conn.execute("SELECT MAX(version) FROM schema_migrations WHERE namespace=?", (namespace,)).fetchone()
    return int(r[0] or 0)


def pending(conn: sqlite3.Connection, sources: dict[str, Path]) -> list[tuple[str, int, Path]]:
    out = []
    for ns, directory in sources.items():
        cur = current_version(conn, ns)
        out += [(ns, v, p) for v, p in discover(directory) if v > cur]
    return out


def split_sql(sql: str) -> list[str]:
    """Split into statements; handles CREATE TRIGGER ... END; blocks."""
    out: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        if not buf and line.strip().startswith("--"):
            continue
        buf.append(line)
        joined = "\n".join(buf).strip()
        if joined.endswith(";") and sqlite3.complete_statement(joined):
            out.append(joined)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def migrate(conn: sqlite3.Connection, sources: dict[str, Path], backup_dir: Path | None = None) -> list[str]:
    todo = pending(conn, sources)
    if not todo:
        return []
    if backup_dir is not None and current_version(conn, CORE_NS) > 0:
        from .backup import snapshot_db
        snapshot_db(conn, backup_dir / f"pre-migrate-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
    applied = []
    for ns, v, p in todo:
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with tx(conn):
                for stmt in split_sql(p.read_text(encoding="utf-8")):
                    conn.execute(stmt)
                conn.execute("INSERT INTO schema_migrations(namespace,version,name,applied_at) VALUES (?,?,?,?)",
                             (ns, v, p.name, utcnow()))
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        applied.append(f"{ns}:{v}")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"foreign key check failed after migration: {[tuple(b) for b in bad[:5]]}")
    return applied


def require_current(conn: sqlite3.Connection, sources: dict[str, Path]) -> None:
    todo = pending(conn, sources)
    if todo:
        raise PendingMigrations("pending migrations " + ", ".join(f"{ns}:{v}" for ns, v, _ in todo) +
                                "; start the API or run `researchos-api migrate` first")
