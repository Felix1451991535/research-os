"""Budget ledger (baseline §8, routing spec §6): reserve -> settled | unknown | released, in DB transactions.

available = monthly cap - settled - open reservations (reserved + unknown). Month boundary Asia/Shanghai.
Optional owner caps (ADR-197): per Asia/Shanghai calendar day and per project (e.g. one acceptance run = one
project); both count settled + open reservations, so an in-flight call already uses its ceiling.
A timeout that may have been billed stays `unknown` (never released and retried); settlement is deduplicated
by the provider response id so a duplicate response never double-charges.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ...contracts.errors import ApiError
from ...kernel.db import Row, jdump, new_id, row, tx, utcnow
from ...kernel.events import emit

SHANGHAI = ZoneInfo("Asia/Shanghai")


def month_key(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(SHANGHAI).strftime("%Y-%m")


@dataclass(frozen=True)
class Budget:
    monthly_cny: float
    per_task_cny: float
    soft_warning_ratio: float
    daily_cny: float | None = None
    per_project_cny: float | None = None


class BudgetExceeded(ApiError):
    def __init__(self, code: str, message: str, details: dict[str, Any]) -> None:
        super().__init__(402, code, message, details=details)


def estimate_cny(price: Row, fx_cny: float, input_tokens: int, max_output_tokens: int) -> float:
    """Conservative ceiling: all input + the full max output at the effective price."""
    cost = (input_tokens * price["input_per_mtok"] + max_output_tokens * price["output_per_mtok"]) / 1_000_000
    return float(round(cost * fx_cny, 6))


def actual_cny(price: Row, fx_cny: float, input_tokens: int, output_tokens: int) -> float:
    cost = (input_tokens * price["input_per_mtok"] + output_tokens * price["output_per_mtok"]) / 1e6
    return float(round(cost * fx_cny, 6))


def month_summary(conn: sqlite3.Connection, month: str | None = None) -> dict[str, float]:
    m = month or month_key()
    r = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN state='settled' THEN actual_cny END),0), "
        "COALESCE(SUM(CASE WHEN state IN ('reserved','unknown') THEN reserved_cny END),0), "
        "COALESCE(SUM(CASE WHEN state='unknown' THEN reserved_cny END),0) FROM ledger_entries WHERE month=?", (m,)
    ).fetchone()
    return {"settled": float(r[0]), "reserved": float(r[1]), "unknown": float(r[2])}


def day_start_utc(now: datetime | None = None) -> str:
    local = (now or datetime.now(UTC)).astimezone(SHANGHAI)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).isoformat()


def _committed(conn: sqlite3.Connection, where: str, args: tuple[Any, ...]) -> float:
    r = conn.execute("SELECT COALESCE(SUM(COALESCE(actual_cny, reserved_cny)),0) FROM ledger_entries "
                     f"WHERE state!='released' AND {where}", args).fetchone()
    return float(r[0])


def day_spent(conn: sqlite3.Connection, now: datetime | None = None) -> float:
    return _committed(conn, "created_at >= ?", (day_start_utc(now),))


def project_spent(conn: sqlite3.Connection, project_id: str) -> float:
    return _committed(conn, "project_id = ?", (project_id,))


def task_spent(conn: sqlite3.Connection, task_id: str | None) -> float:
    if not task_id:
        return 0.0
    r = conn.execute("SELECT COALESCE(SUM(COALESCE(actual_cny, reserved_cny)),0) FROM ledger_entries "
                     "WHERE task_id=? AND state!='released'", (task_id,)).fetchone()
    return float(r[0])


def reserve(conn: sqlite3.Connection, *, call_id: str, amount: float, budget: Budget, snapshot: dict[str, Any],
            project_id: str | None, task_id: str | None, cost_kind: str = "model_call") -> str:
    """Atomically check budgets and reserve. Concurrent reservations serialize on the write lock."""
    with tx(conn):
        m = month_key()
        s = month_summary(conn, m)
        available = budget.monthly_cny - s["settled"] - s["reserved"]
        if amount > available:
            raise BudgetExceeded("monthly_budget_exhausted", "monthly AI budget would be exceeded; paid call rejected",
                                 {"available_cny": round(available, 4), "needed_cny": amount})
        if task_id and task_spent(conn, task_id) + amount > budget.per_task_cny:
            raise BudgetExceeded("task_budget_exhausted", "per-task AI budget would be exceeded",
                                 {"per_task_cny": budget.per_task_cny})
        if budget.daily_cny is not None and day_spent(conn) + amount > budget.daily_cny:
            raise BudgetExceeded("daily_budget_exhausted", "today's AI budget would be exceeded; paid call rejected",
                                 {"daily_cny": budget.daily_cny, "needed_cny": amount})
        if budget.per_project_cny is not None and (
                project_id is None or project_spent(conn, project_id) + amount > budget.per_project_cny):
            raise BudgetExceeded("project_budget_exhausted", "per-project AI budget would be exceeded (or the call "
                                 "has no project)", {"per_project_cny": budget.per_project_cny})
        eid = new_id("led")
        conn.execute("INSERT INTO ledger_entries(id,call_id,cost_kind,state,month,reserved_cny,price_snapshot,"
                     "project_id,task_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (eid, call_id, cost_kind, "reserved", m, amount, jdump(snapshot), project_id, task_id, utcnow()))
        spent = s["settled"] + s["reserved"] + amount
        if spent >= budget.soft_warning_ratio * budget.monthly_cny:
            emit(conn, "budget.soft_warning", entity_type="ledger", entity_id=eid,
                 data={"month": m, "committed_cny": round(spent, 4), "cap_cny": budget.monthly_cny})
        return eid


def settle(conn: sqlite3.Connection, entry_id: str, cost: float, response_id: str | None) -> bool:
    """Returns False if this provider response was already settled (duplicate) — then the entry is released."""
    with tx(conn):
        if response_id and row(conn.execute("SELECT id FROM ledger_entries WHERE provider_response_id=?",
                                            (response_id,))):
            conn.execute("UPDATE ledger_entries SET state='released', settled_at=? WHERE id=? AND state='reserved'",
                         (utcnow(), entry_id))
            return False
        conn.execute("UPDATE ledger_entries SET state='settled', actual_cny=?, provider_response_id=?, settled_at=? "
                     "WHERE id=? AND state IN ('reserved','unknown')", (cost, response_id, utcnow(), entry_id))
        return True


def mark_unknown(conn: sqlite3.Connection, entry_id: str) -> None:
    with tx(conn):
        conn.execute("UPDATE ledger_entries SET state='unknown' WHERE id=? AND state='reserved'", (entry_id,))


def release(conn: sqlite3.Connection, entry_id: str) -> None:
    """Only when the provider certainly did not bill (request rejected before processing)."""
    with tx(conn):
        conn.execute("UPDATE ledger_entries SET state='released', settled_at=? WHERE id=? AND state='reserved'",
                     (utcnow(), entry_id))


def acquire_slot(conn: sqlite3.Connection, holder: str, hold_s: float) -> bool:
    """One paid call in flight across processes. Expired holders (crash) are taken over."""
    now = datetime.now(UTC)
    until = (now + timedelta(seconds=hold_s)).isoformat(timespec="milliseconds")
    with tx(conn):
        cur = conn.execute("UPDATE gateway_slot SET holder=?, until=? WHERE id=1 AND (holder IS NULL OR until<?)",
                           (holder, until, now.isoformat(timespec="milliseconds")))
        return cur.rowcount == 1


def release_slot(conn: sqlite3.Connection, holder: str) -> None:
    with tx(conn):
        conn.execute("UPDATE gateway_slot SET holder=NULL, until=NULL WHERE id=1 AND holder=?", (holder,))
