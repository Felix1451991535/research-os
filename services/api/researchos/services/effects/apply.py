"""Journaled apply, crash recovery and undo of an approved EffectPlan (ARCHITECTURE §7, K4).

apply: approval (hash-bound) -> per op: skip if done; re-check preconditions; intent row; act; verify; done row.
  Blocked (locked file) pauses the plan and runs nothing after it; a Conflict fails the plan (later ops not run).
recover: an op with `intent` and no result is resolved from the world: target present with the recorded hash ->
  done; our *.ros-partial -> removed and the op runs again. The plan becomes `interrupted`; resuming is an owner action.
undo: done ops in reverse; each undo validates the current state against the journal and reports conflicts instead
  of forcing (copies go to the trash, moves go back, created empty folders are removed)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ...contracts.errors import ApiError
from ...kernel.db import row, utcnow
from . import fsops, journal
from .plan import get

FINAL = ("done", "skipped", "recovered")


def approved(conn: sqlite3.Connection, plan: dict[str, Any]) -> bool:
    a = row(conn.execute("SELECT subject_hash FROM approvals WHERE subject_type='effect_plan' AND subject_id=? AND "
                         "revoked_at IS NULL AND expires_at>? ORDER BY created_at DESC LIMIT 1",
                         (plan["id"], utcnow())))
    return a is not None and a["subject_hash"] == plan["plan_hash"]


def _act(op: dict[str, Any]) -> dict[str, Any]:
    k = op["op"]
    if k == "mkdir":
        return fsops.mkdir(Path(op["path"]))
    if k == "copy":
        return fsops.copy(Path(op["src"]), Path(op["dst"]), op["sha256"])
    if k == "move":
        return fsops.move(Path(op["src"]), Path(op["dst"]), op["sha256"])
    return fsops.trash(Path(op["path"]), op.get("sha256"))


def _recover_op(op: dict[str, Any]) -> dict[str, Any] | None:
    """Return a 'recovered' detail if the interrupted op actually completed; else clean our temp and return None."""
    k = op["op"]
    if k == "mkdir":
        return {"existed": False} if Path(op["path"]).is_dir() else None
    if k == "trash":
        return {"trashed": op["path"]} if not Path(op["path"]).exists() else None
    dst, src = Path(op["dst"]), Path(op["src"])
    fsops.partial_of(dst).unlink(missing_ok=True)  # ours, never user data
    if dst.is_file() and fsops.sha256_file(dst) == op["sha256"]:
        if k == "move" and src.is_file():  # crashed between link and unlink: same data under both names
            src.unlink()
        return {"dst_sha256": op["sha256"]}
    return None


def recover(conn: sqlite3.Connection, pid: str, actor: str) -> list[int]:
    plan, state = get(conn, pid), journal.last(conn, pid)
    fixed = []
    for i, op in enumerate(plan["ops"]):
        if state.get(i, {}).get("phase") == "intent":
            detail = _recover_op(op)
            if detail is not None:
                journal.write(conn, pid, i, "recovered", actor, detail)
                fixed.append(i)
            else:
                journal.write(conn, pid, i, "skipped", actor, {"retry": True, "reason": "interrupted before done"})
    if plan["state"] == "applying":
        journal.set_state(conn, pid, "interrupted", actor, "worker stopped during apply; owner must resume")
    return fixed


def apply(conn: sqlite3.Connection, pid: str, actor: str) -> dict[str, Any]:
    plan = get(conn, pid)
    if plan["state"] not in ("proposed", "paused", "interrupted", "applying"):
        raise ApiError(409, "bad_state", f"plan is {plan['state']}")
    if not approved(conn, plan):
        raise ApiError(409, "effect_not_approved", "approve this exact plan first (re-auth on remote devices)",
                       details={"subject_type": "effect_plan", "subject_id": pid})
    if any(v["phase"] == "intent" for v in journal.last(conn, pid).values()):
        recover(conn, pid, actor)
    journal.set_state(conn, pid, "applying", actor)
    for i, op in enumerate(plan["ops"]):
        last = journal.last(conn, pid).get(i, {})
        if last.get("phase") in FINAL and not last.get("detail", {}).get("retry"):
            continue
        journal.write(conn, pid, i, "intent", actor, {"op": op["op"]})
        try:
            detail = _act(op)
        except fsops.Blocked as e:
            journal.write(conn, pid, i, "blocked", actor, {"reason": str(e)})
            journal.set_state(conn, pid, "paused", actor, str(e))
            return {"state": "paused", "blocked_op": i, "reason": str(e)}
        except (fsops.Conflict, OSError) as e:
            journal.write(conn, pid, i, "failed", actor, {"reason": str(e)})
            journal.set_state(conn, pid, "failed", actor, str(e))
            return {"state": "failed", "failed_op": i, "reason": str(e)}
        journal.write(conn, pid, i, "done", actor, detail)
    journal.set_state(conn, pid, "applied", actor)
    return {"state": "applied", "ops": len(plan["ops"])}


def _undo_op(op: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    k = op["op"]
    if k == "copy":
        return fsops.trash(Path(op["dst"]), op["sha256"])
    if k == "move":
        if Path(op["src"]).exists():
            raise fsops.Conflict(f"original location is occupied again: {op['src']}")
        return fsops.move(Path(op["dst"]), Path(op["src"]), op["sha256"])
    if k == "mkdir":
        p = Path(op["path"])
        if detail.get("existed") or not p.is_dir():
            return {"kept": True}
        if any(p.iterdir()):
            raise fsops.Conflict(f"folder is not empty any more: {p}")
        p.rmdir()  # an empty folder this plan created: no user data
        return {"removed": str(p)}
    raise fsops.Conflict(f"trashed item cannot be restored automatically; restore {op['path']} from the system trash")


def undo(conn: sqlite3.Connection, pid: str, actor: str) -> dict[str, Any]:
    plan = get(conn, pid)
    if plan["state"] not in ("applied", "failed", "paused", "interrupted", "undo_conflict"):
        raise ApiError(409, "bad_state", f"plan is {plan['state']}")
    journal.set_state(conn, pid, "undoing", actor)
    conflicts = []
    state = journal.last(conn, pid)
    for i in reversed(range(len(plan["ops"]))):
        last = state.get(i, {})
        if last.get("phase") not in ("done", "recovered", "undo_conflict"):
            continue
        journal.write(conn, pid, i, "undo_intent", actor, {})
        try:
            journal.write(conn, pid, i, "undone", actor, _undo_op(plan["ops"][i], last.get("detail") or {}))
        except (fsops.Conflict, fsops.Blocked, OSError) as e:
            journal.write(conn, pid, i, "undo_conflict", actor, {"reason": str(e)})
            conflicts.append({"op": i, "reason": str(e)})
    journal.set_state(conn, pid, "undo_conflict" if conflicts else "undone", actor,
                      f"{len(conflicts)} op(s) could not be undone" if conflicts else None)
    return {"state": "undo_conflict" if conflicts else "undone", "conflicts": conflicts}
