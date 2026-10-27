"""Daily and per-project ledger caps (ADR-197): enforced at reservation, before any provider call."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from researchos.contracts.errors import ApiError
from researchos.services.gateway import ledger


def _reserve(conn: sqlite3.Connection, b: ledger.Budget, amount: float, project: str | None = "p1") -> str:
    return ledger.reserve(conn, call_id=None, amount=amount, budget=b, snapshot={},  # type: ignore[arg-type]
                          project_id=project, task_id=None)


def test_daily_cap_counts_today_only(conn: sqlite3.Connection) -> None:
    b = ledger.Budget(100, 100, 0.99, daily_cny=10.0)
    old = _reserve(conn, b, 9)
    yesterday = (datetime.fromisoformat(ledger.day_start_utc()) - timedelta(minutes=1)).isoformat()
    conn.execute("UPDATE ledger_entries SET created_at=? WHERE id=?", (yesterday, old))
    conn.commit()
    _reserve(conn, b, 6)
    ledger.settle(conn, _reserve(conn, b, 3), 2.0, "r1")  # settled at its actual cost
    with pytest.raises(ApiError) as e:
        _reserve(conn, b, 2.5)  # 6 + 2 + 2.5 > 10
    assert e.value.code == "daily_budget_exhausted" and e.value.status == 402
    _reserve(conn, b, 2.0)


def test_day_boundary_is_asia_shanghai() -> None:
    # 2026-10-17 16:30 UTC is 00:30 on 10-18 in Shanghai: the day started at 16:00 UTC
    assert ledger.day_start_utc(datetime(2026, 10, 17, 16, 30, tzinfo=UTC)).startswith("2026-10-17T16:00:00")


def test_project_cap_and_calls_without_project(conn: sqlite3.Connection) -> None:
    b = ledger.Budget(100, 100, 0.99, per_project_cny=5.0)
    _reserve(conn, b, 4, "p1")
    _reserve(conn, b, 4, "p2")  # other project unaffected
    for project, code in (("p1", "project_budget_exhausted"), (None, "project_budget_exhausted")):
        with pytest.raises(ApiError) as e:
            _reserve(conn, b, 1.5, project)
        assert e.value.code == code
    released = _reserve(conn, b, 1, "p1")
    ledger.release(conn, released)
    _reserve(conn, b, 1, "p1")  # released reservations do not count


def test_policy_round_trip(client: Any) -> None:
    body = {"monthly_budget_cny": 50, "per_task_budget_cny": 5, "daily_budget_cny": 14.5,
            "per_project_budget_cny": 7.2}
    assert client.put("/api/v1/gateway/policy", json=body).status_code == 200
    got = client.get("/api/v1/gateway/policy").json()
    assert got["daily_budget_cny"] == 14.5 and got["per_project_budget_cny"] == 7.2
