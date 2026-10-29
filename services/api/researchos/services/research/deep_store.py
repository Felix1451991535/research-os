"""Persistence helpers for deep research runs. Concurrency (ADR-231, ADR-239): an advance first claims the run (a
lease in advance_owner/advance_until, taken atomically) and gets a unique claim token; every write of the advance
(`save`, `move`, `renew`) and its `release` must present that token, so an advance whose lease was taken over can no
longer write or free the run (fencing), and every write renews the lease. Stage changes go through `move`, a
compare-and-swap on the stage being left, so two advances can never both move a stage."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from ...contracts.errors import not_found
from ...kernel.db import Row, jdump, jload, new_id, row, tx, utcnow
from ...kernel.events import emit

LEASE_S = 900  # renewed by every write and between the units of a long stage; a dead worker's claim expires and
# the next trigger tick continues the run


class StageConflict(Exception):
    """The run is no longer in the stage this advance started from (another advance moved it)."""


class LeaseLost(StageConflict):
    """This advance's claim expired and was taken over: it may not write to the run any more."""


def get(conn: sqlite3.Connection, rid: str) -> Row:
    r = row(conn.execute("SELECT * FROM deep_research WHERE id=?", (rid,)))
    if r is None:
        raise not_found("deep research run")
    out = {k: v for k, v in r.items() if k not in ("advance_owner", "advance_until")}  # internal claim columns
    return {**out, "settings": jload(r["settings"]), "plan": jload(r["plan"]), "data": jload(r["data"]) or {}}


def _sets(fields: dict[str, Any]) -> dict[str, Any]:
    if "stage" in fields:
        raise ValueError("stage changes go through move()")
    return {k: jdump(v) if k in ("plan", "data", "settings") else v for k, v in fields.items()}


def _until(lease_s: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=lease_s)).isoformat(timespec="milliseconds")


def _fenced(conn: sqlite3.Connection, rid: str, token: str) -> None:
    """Raise LeaseLost if `token` no longer holds the run (only called after a write matched no row)."""
    held = row(conn.execute("SELECT advance_owner FROM deep_research WHERE id=?", (rid,)))
    if held is None or held["advance_owner"] != token:
        raise LeaseLost(f"{rid}: claim {token} was taken over")


def save(conn: sqlite3.Connection, rid: str, actor: str, *, token: str, join: bool = False, **fields: Any) -> Row:
    """Update stage outputs (plan, data, settings) without changing the stage; only the claim holder (`token`) can,
    and the write renews its lease. `join`: part of the caller's open transaction (LeaseLost rolls it back)."""
    sets = _sets(fields)
    with tx(conn, join=join):
        cur = conn.execute(f"UPDATE deep_research SET {', '.join(f'{k}=?' for k in sets)}, updated_at=?, "
                           "advance_until=? WHERE id=? AND advance_owner=?",
                           (*sets.values(), utcnow(), _until(LEASE_S), rid, token))
        if cur.rowcount != 1:
            _fenced(conn, rid, token)
    return get(conn, rid)


def move(conn: sqlite3.Connection, rid: str, actor: str, frm: str, to: str, *, token: str, **fields: Any) -> Row:
    """Compare-and-swap `frm` -> `to` (with optional outputs) by the claim holder `token`; LeaseLost if the claim was
    taken over, StageConflict if the run is no longer in `frm`."""
    sets = {**_sets(fields), "stage": to}
    with tx(conn):
        cur = conn.execute(f"UPDATE deep_research SET {', '.join(f'{k}=?' for k in sets)}, updated_at=?, "
                           "advance_until=? WHERE id=? AND stage=? AND advance_owner=?",
                           (*sets.values(), utcnow(), _until(LEASE_S), rid, frm, token))
        if cur.rowcount != 1:
            _fenced(conn, rid, token)
            raise StageConflict(f"{rid}: expected stage {frm}")
        emit(conn, f"deep_research.{to}", actor=actor, entity_type="deep_research", entity_id=rid, data={"from": frm})
    return get(conn, rid)


def claim(conn: sqlite3.Connection, rid: str, lease_s: float = LEASE_S) -> str | None:
    """Take the run for one advance: a unique claim token, or None while another advance holds an unexpired claim."""
    token, now = new_id("adv"), datetime.now(UTC).isoformat(timespec="milliseconds")
    with tx(conn):
        cur = conn.execute("UPDATE deep_research SET advance_owner=?, advance_until=? WHERE id=? AND "
                           "(advance_owner IS NULL OR advance_until < ?)", (token, _until(lease_s), rid, now))
    return token if cur.rowcount == 1 else None


def renew(conn: sqlite3.Connection, rid: str, token: str, lease_s: float = LEASE_S) -> None:
    """Extend the claim between the units of a long stage; LeaseLost if it was taken over."""
    with tx(conn):
        cur = conn.execute("UPDATE deep_research SET advance_until=? WHERE id=? AND advance_owner=?",
                           (_until(lease_s), rid, token))
    if cur.rowcount != 1:
        raise LeaseLost(f"{rid}: claim {token} was taken over")


def release(conn: sqlite3.Connection, rid: str, token: str) -> None:
    """Free the run; a no-op for a token that no longer holds it (never frees a replacement's claim)."""
    with tx(conn):
        conn.execute("UPDATE deep_research SET advance_owner=NULL, advance_until=NULL WHERE id=? AND advance_owner=?",
                     (rid, token))


def plan_hash(r: Row) -> str:
    return hashlib.sha256(jdump({"question": r["question"], "plan": r["plan"], "settings": r["settings"]})
                          .encode()).hexdigest()


def checkpoint_hash(conn: sqlite3.Connection, subject_id: str) -> str:
    """Approval resolver for `research_checkpoint`: subject id `<run id>:plan` binds question + plan + settings."""
    rid, _, what = subject_id.partition(":")
    if what != "plan":
        raise ValueError("unknown research checkpoint")
    return plan_hash(get(conn, rid))
