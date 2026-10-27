"""Backup / restore (baseline §4, §11).

Backup = consistent SQLite snapshot via the online backup API (never a copy of the live .db file) + copies of
originals (raw/), experiment records (runs/), exports/, datasets registry files and non-secret config, with a
sha256 manifest. Rebuildable caches (parsed/, cache/), logs and provider keys are excluded.

Restore always goes into a NEW directory, verifies every file hash and every DB reference (document files,
artifacts) before the caller is allowed to switch the live data dir.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

INCLUDED_DIRS = ("raw", "runs", "exports", "datasets")
EXCLUDED_CONFIG = {"secrets.env", "setup-token.txt"}
MANIFEST = "manifest.json"
DB_NAME = "researchos.db"


def file_sha256(path: Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(bufsize):
            h.update(chunk)
    return h.hexdigest()


def snapshot_db(conn: sqlite3.Connection, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    out = sqlite3.connect(str(tmp))
    try:
        conn.backup(out)
        out.execute("PRAGMA journal_mode=DELETE")
    finally:
        out.close()
    os.replace(tmp, dest)
    return dest


def _walk(base: Path) -> Iterator[Path]:
    for root, dirs, files in os.walk(base, followlinks=False):
        dirs.sort()
        for f in sorted(files):
            p = Path(root) / f
            if p.is_symlink():
                continue  # never follow links out of the data dir
            yield p


def _active_run_dirs(conn: sqlite3.Connection) -> set[str]:
    """runs/<run_id> of tasks still running: their files are incomplete and are excluded from the backup."""
    try:
        cur = conn.execute("SELECT r.id FROM runs r JOIN tasks t ON t.id=r.task_id "
                           "WHERE t.state IN ('running','cancelling') AND r.ended_at IS NULL")
    except sqlite3.OperationalError:
        return set()
    return {f"runs/{r[0]}/" for r in cur.fetchall()}


def create_backup(settings: Any, conn: sqlite3.Connection, label: str = "") -> dict[str, Any]:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{ts}{'-' + label if label else ''}"
    dest = settings.backups_dir / name
    tmp = settings.backups_dir / (name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    files: dict[str, dict[str, Any]] = {}
    active = _active_run_dirs(conn)
    snapshot_db(conn, tmp / "db" / DB_NAME)
    for sub in INCLUDED_DIRS:
        src = settings.data_dir / sub
        if not src.is_dir():
            continue
        for p in _walk(src):
            rel = p.relative_to(settings.data_dir).as_posix()
            if "/_tmp/" in "/" + rel or rel.endswith(".partial") or any(rel.startswith(a) for a in active):
                continue
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    if settings.config_dir.is_dir():
        for p in settings.config_dir.iterdir():
            if p.is_file() and p.name not in EXCLUDED_CONFIG:
                (tmp / "config").mkdir(exist_ok=True)
                shutil.copy2(p, tmp / "config" / p.name)
    for p in _walk(tmp):
        rel = p.relative_to(tmp).as_posix()
        files[rel] = {"sha256": file_sha256(p), "size": p.stat().st_size}
    schema = conn.execute("SELECT MAX(version) FROM schema_migrations WHERE namespace='core'").fetchone()[0]
    manifest = {"format": 1, "created_at": datetime.now().astimezone().isoformat(), "schema_version": schema,
                "source_data_dir": str(settings.data_dir), "files": files,
                "excluded_active_runs": sorted(active),
                "excluded": ["parsed/ (rebuildable)", "cache/", "logs/", "backups/",
                             "config/secrets.env (re-enter keys)"]}
    (tmp / MANIFEST).write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, dest)
    return {"path": str(dest), "files": len(files), "bytes": sum(f["size"] for f in files.values()),
            "schema_version": schema}


def verify_tree(root: Path) -> list[str]:
    """Check every file listed in the manifest exists with matching sha256."""
    problems = []
    mf = root / MANIFEST
    if not mf.is_file():
        return [f"manifest missing in {root}"]
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    for rel, meta in manifest["files"].items():
        p = root / rel
        if not p.is_file():
            problems.append(f"missing: {rel}")
        elif file_sha256(p) != meta["sha256"]:
            problems.append(f"hash mismatch: {rel}")
    return problems


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _file_refs(conn: sqlite3.Connection, data_dir: Path, sql: str, label: str) -> list[str]:
    problems = []
    for ref_id, rel, sha in conn.execute(sql):
        p = data_dir / rel
        if not p.is_file():
            problems.append(f"{label} missing: {ref_id} {rel}")
        elif file_sha256(p) != sha:
            problems.append(f"{label} hash mismatch: {ref_id}")
    return problems


def verify_db_refs(data_dir: Path) -> list[str]:
    """Every DB reference to a file (document originals, artifacts) must resolve inside data_dir with matching hash;
    evidence chunks must point at existing document versions (citations survive restore)."""
    problems: list[str] = []
    conn = sqlite3.connect(str(data_dir / "db" / DB_NAME))
    try:
        qc = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if qc != "ok":
            problems.append(f"integrity_check: {qc}")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            problems.append("foreign_key_check: violations")
        if _table_exists(conn, "document_versions"):
            problems += _file_refs(conn, data_dir, "SELECT version_id, stored_path, sha256 FROM document_versions",
                                   "document")
        if _table_exists(conn, "artifacts"):
            problems += _file_refs(conn, data_dir, "SELECT id, path, sha256 FROM artifacts", "artifact")
        if _table_exists(conn, "chunks"):
            n = conn.execute("SELECT COUNT(*) FROM chunks c LEFT JOIN document_versions v ON v.version_id=c.version_id "
                             "WHERE v.version_id IS NULL").fetchone()[0]
            if n:
                problems.append(f"{n} chunks reference missing document versions")
    finally:
        conn.close()
    return problems


def restore_backup(backup_dir: Path, target_data_dir: Path) -> dict[str, Any]:
    """Restore into a new empty directory and verify. Does NOT switch the live data dir."""
    backup_dir, target = Path(backup_dir), Path(target_data_dir)
    problems = verify_tree(backup_dir)
    if problems:
        return {"ok": False, "stage": "verify_backup", "problems": problems}
    if target.exists() and any(target.iterdir()):
        return {"ok": False, "stage": "target", "problems": [f"target {target} is not empty; restore needs a new dir"]}
    target.mkdir(parents=True, exist_ok=True)
    for p in _walk(backup_dir):
        rel = p.relative_to(backup_dir)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    for sub in ("parsed", "cache", "logs", "backups", "config", "incoming"):
        (target / sub).mkdir(exist_ok=True)
    problems = verify_tree(target) + verify_db_refs(target)
    return {"ok": not problems, "stage": "verified" if not problems else "verify_restored", "problems": problems,
            "target": str(target)}
