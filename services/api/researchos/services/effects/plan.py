"""EffectPlan: typed ops with preconditions -> deterministic validation -> dry-run diff -> immutable plan row whose
hash is the K3 approval subject (`effect_plan`). Ops: mkdir{path} copy{src,dst,sha256,size} move{...}
trash{path,sha256}. Validation: roots and access, Windows-safe names, never-overwrite, duplicate/case-colliding
targets, move cycles / source reuse, parent folders, free space, at most 500 ops."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...kernel.db import Row, jdump, jload, new_id, row, tx, utcnow
from ...kernel.events import emit
from ...platform.paths import compare_key
from . import roots

MAX_OPS = 500
NEED = {"mkdir": {"path": "write"}, "copy": {"src": "read", "dst": "write"}, "move": {"src": "write", "dst": "write"},
        "trash": {"path": "write"}}


def plan_hash(ops: list[dict[str, Any]]) -> str:
    return hashlib.sha256(jdump(ops).encode()).hexdigest()


def _paths(conn: sqlite3.Connection, op: dict[str, Any]) -> dict[str, Path]:
    kind = op.get("op")
    if kind not in NEED:
        raise ApiError(422, "bad_op", f"unknown op {kind!r} (closed set: {sorted(NEED)})")
    out = {}
    for field, access in NEED[kind].items():
        if not isinstance(op.get(field), str):
            raise ApiError(422, "bad_op", f"{kind} needs {field}")
        must_exist = field == "src" or kind == "trash"
        out[field] = roots.resolve(conn, op[field], access, must_exist=must_exist)
    if kind in ("copy", "move", "trash") and not isinstance(op.get("sha256"), str):
        raise ApiError(422, "bad_op", f"{kind} needs the source sha256 precondition")
    return out


class _State:
    def __init__(self) -> None:
        self.targets: dict[str, int] = {}
        self.sources: set[str] = set()
        self.created_dirs: set[str] = set()
        self.copy_bytes: dict[Path, int] = {}


def _target(st: _State, i: int, p: Path) -> None:
    k = compare_key(str(p), windows=True)  # libraries must stay valid on Windows: case-insensitive collisions
    if k in st.targets:
        raise ApiError(422, "target_collision", f"ops {st.targets[k]} and {i} write the same target {p}")
    if k in st.sources:
        raise ApiError(422, "move_cycle", f"op {i} writes {p}, which another op reads or moves")
    st.targets[k] = i


def _check_op(conn: sqlite3.Connection, st: _State, i: int, op: dict[str, Any]) -> None:
    ps = _paths(conn, op)
    dst = ps.get("dst") or (ps["path"] if op["op"] == "mkdir" else None)
    if "src" in ps:
        k = compare_key(str(ps["src"]), windows=True)
        if k in st.targets or (op["op"] == "move" and k in st.sources):
            raise ApiError(422, "move_cycle", f"op {i} reads {ps['src']}, which another op writes or moves")
        st.sources.add(k)
    if dst is None:
        return
    _target(st, i, dst)
    parent = compare_key(str(dst.parent), windows=True)
    if not dst.parent.is_dir() and parent not in st.created_dirs:
        raise ApiError(422, "missing_parent", f"op {i}: create {dst.parent} first (mkdir op)")
    if op["op"] == "mkdir":
        if dst.exists() and not dst.is_dir():
            raise ApiError(422, "target_exists", f"op {i}: a file exists at {dst}")
        st.created_dirs.add(compare_key(str(dst), windows=True))
    elif dst.exists() or Path(str(dst) + ".ros-partial").exists():
        raise ApiError(422, "target_exists", f"op {i}: {dst} exists (never overwritten)")
    if op["op"] == "copy":
        anchor = next(p for p in (dst.parent, *dst.parents) if p.exists())
        st.copy_bytes[anchor] = st.copy_bytes.get(anchor, 0) + int(op.get("size") or 0)


def validate(conn: sqlite3.Connection, ops: list[dict[str, Any]]) -> None:
    if not ops or len(ops) > MAX_OPS:
        raise ApiError(422, "bad_plan", f"a plan has 1..{MAX_OPS} ops")
    st = _State()
    for i, op in enumerate(ops):
        _check_op(conn, st, i, op)
    for anchor, need in st.copy_bytes.items():
        free = shutil.disk_usage(anchor).free
        if need > free * 0.95:
            raise ApiError(422, "no_space", f"{need} bytes needed under {anchor}, {free} free")


def diff(ops: list[dict[str, Any]], notes: list[str] | None = None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for op in ops:
        counts[op["op"]] = counts.get(op["op"], 0) + 1
    lines = [f"{op['op']:5} {op.get('src', '')} -> {op.get('dst', op.get('path', ''))}" for op in ops[:300]]
    return {"counts": counts, "bytes_copied": sum(int(o.get("size") or 0) for o in ops if o["op"] == "copy"),
            "lines": lines, "truncated": max(0, len(ops) - 300), "notes": notes or []}


def propose(conn: sqlite3.Connection, *, kind: str, title: str, ops: list[dict[str, Any]], actor: str,
            notes: list[str] | None = None, project_id: str | None = None) -> Row:
    validate(conn, ops)
    pid, h, now = new_id("eff"), plan_hash(ops), utcnow()
    with tx(conn):
        conn.execute("INSERT INTO effect_plans(id,project_id,kind,title,ops,plan_hash,diff,state,created_by,"
                     "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (pid, project_id, kind, title, jdump(ops), h, jdump(diff(ops, notes)), "proposed", actor, now,
                      now))
        emit(conn, "effect.proposed", actor=actor, entity_type="effect_plan", entity_id=pid, project_id=project_id,
             data={"kind": kind, "ops": len(ops), "hash": h})
    return get(conn, pid)


def get(conn: sqlite3.Connection, pid: str) -> Row:
    p = row(conn.execute("SELECT * FROM effect_plans WHERE id=?", (pid,)))
    if p is None:
        raise not_found("effect plan")
    return {**p, "ops": jload(p["ops"]), "diff": jload(p["diff"])}


def subject_hash(conn: sqlite3.Connection, subject_id: str) -> str:
    """Approval subject resolver for `effect_plan`: the subject id is the plan id; its hash is the ops hash."""
    return str(get(conn, subject_id)["plan_hash"])
