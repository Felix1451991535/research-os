"""Deep research stage concurrency (ADR-231, ADR-239): one advance per run at a time (claim with a unique token),
stage changes are compare-and-swap, and every write / release must present the current token, so two workers
advancing the same run never both run or move a stage and a stale advance can neither overwrite the replacement's
outputs nor free its claim. The stage body is replaced by a blocking stub (no sources, no models)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from researchos.kernel.config import Settings
from researchos.kernel.db import connect, utcnow
from researchos.services.research import deep
from researchos.services.research import deep_store as store


@pytest.fixture
def run_id(conn: sqlite3.Connection) -> str:
    now = utcnow()
    conn.execute("INSERT INTO projects(id, title, created_at, updated_at) VALUES ('p1', 'P', ?, ?)", (now, now))
    conn.execute("INSERT INTO deep_research(id, project_id, question, settings, plan, stage, data, created_by, "
                 "created_at, updated_at) VALUES ('dr1', 'p1', 'q?', '{}', '{\"sub_questions\": []}', 'searching', "
                 "'{}', 'test', ?, ?)", (now, now))
    conn.commit()
    return "dr1"


@pytest.fixture
def blocking_search(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"entered": threading.Event(), "go": threading.Event(), "calls": 0}

    def searching(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str, token: str) -> str:
        state["calls"] += 1
        state["entered"].set()
        assert state["go"].wait(10)
        store.move(conn, r["id"], actor, "searching", "awaiting_screening", token=token)
        return "awaiting_screening"

    monkeypatch.setitem(deep.STAGES, "searching", searching)
    yield state
    state["go"].set()


def _advance(settings: Settings, rid: str, actor: str, out: dict[str, str]) -> None:
    c = connect(settings.db_path)
    try:
        out[actor] = deep.advance(c, None, rid, actor)
    finally:
        c.close()


def test_two_workers_one_runs_the_stage(settings: Settings, run_id: str, blocking_search: dict[str, Any]) -> None:
    out: dict[str, str] = {}
    a = threading.Thread(target=_advance, args=(settings, run_id, "task:a", out))
    a.start()
    assert blocking_search["entered"].wait(10)
    _advance(settings, run_id, "task:b", out)  # while a holds the run
    assert out["task:b"] == "searching" and blocking_search["calls"] == 1
    blocking_search["go"].set()
    a.join(10)
    assert out["task:a"] == "awaiting_screening" and blocking_search["calls"] == 1
    c = connect(settings.db_path)
    try:
        assert store.get(c, run_id)["stage"] == "awaiting_screening"
        moves = c.execute("SELECT count(*) FROM events WHERE kind='deep_research.awaiting_screening'").fetchone()[0]
        assert moves == 1
        assert c.execute("SELECT advance_owner FROM deep_research WHERE id=?", (run_id,)).fetchone()[0] is None
    finally:
        c.close()


def test_stage_move_is_compare_and_swap(conn: sqlite3.Connection, run_id: str) -> None:
    token = store.claim(conn, run_id)
    assert token
    store.move(conn, run_id, "t", "searching", "awaiting_screening", token=token)
    with pytest.raises(store.StageConflict):
        store.move(conn, run_id, "t", "searching", "awaiting_screening", token=token)
    with pytest.raises(ValueError, match="move"):
        store.save(conn, run_id, "t", token=token, stage="completed")
    assert store.get(conn, run_id)["stage"] == "awaiting_screening"
    assert "advance_owner" not in store.get(conn, run_id)


def test_a_late_advance_whose_stage_moved_does_not_move_it_again(conn: sqlite3.Connection, run_id: str,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """E.g. a claim that expired during a very long stage: the CAS still stops a second move."""
    def searching(c: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str, token: str) -> str:
        c.execute("UPDATE deep_research SET stage='awaiting_screening' WHERE id=?", (r["id"],))  # moved elsewhere
        c.commit()
        store.move(c, r["id"], actor, "searching", "awaiting_screening", token=token)
        return "unreachable"

    monkeypatch.setitem(deep.STAGES, "searching", searching)
    assert deep.advance(conn, None, run_id, "task:late") == "awaiting_screening"
    assert store.claim(conn, run_id)  # the late advance released its claim


def test_an_expired_claim_can_be_taken_over(conn: sqlite3.Connection, run_id: str) -> None:
    dead = store.claim(conn, run_id, lease_s=-1)
    new = store.claim(conn, run_id)
    assert dead and new and dead != new
    assert store.claim(conn, run_id) is None
    store.release(conn, run_id, dead)  # a stale owner cannot release someone else's claim
    assert store.claim(conn, run_id) is None


def test_same_actor_gets_a_new_token_and_its_stale_advance_cannot_release_it(conn: sqlite3.Connection,
                                                                            run_id: str) -> None:
    """Review #93: claims were keyed by the actor, so a retried task (same actor) could free its replacement's lease."""
    stale = store.claim(conn, run_id, lease_s=-1)
    fresh = store.claim(conn, run_id)
    assert stale and fresh and stale != fresh
    store.release(conn, run_id, stale)
    held = conn.execute("SELECT advance_owner FROM deep_research WHERE id=?", (run_id,)).fetchone()[0]
    assert held == fresh


def test_a_stale_advance_cannot_save_move_or_renew(conn: sqlite3.Connection, run_id: str) -> None:
    """Review #93: an advance whose lease expired and was taken over may not overwrite the winner's outputs."""
    stale = store.claim(conn, run_id, lease_s=-1)
    fresh = store.claim(conn, run_id)
    assert stale and fresh
    store.save(conn, run_id, "task:new", token=fresh, data={"round": 1, "by": "winner"})
    with pytest.raises(store.LeaseLost):
        store.save(conn, run_id, "task:old", token=stale, data={"by": "stale"})
    with pytest.raises(store.LeaseLost):
        store.move(conn, run_id, "task:old", "searching", "awaiting_screening", token=stale)
    with pytest.raises(store.LeaseLost):
        store.renew(conn, run_id, stale)
    r = store.get(conn, run_id)
    assert r["data"] == {"round": 1, "by": "winner"} and r["stage"] == "searching"


def test_writes_and_renew_extend_the_lease(conn: sqlite3.Connection, run_id: str) -> None:
    token = store.claim(conn, run_id, lease_s=-1)  # expired, but nobody took it over yet
    assert token
    store.renew(conn, run_id, token)
    assert store.claim(conn, run_id) is None  # renewed: no takeover
    conn.execute("UPDATE deep_research SET advance_until='2000-01-01T00:00:00.000+00:00' WHERE id=?", (run_id,))
    conn.commit()
    store.save(conn, run_id, "t", token=token, data={"x": 1})  # a write renews as well
    assert store.claim(conn, run_id) is None


def test_a_long_stage_that_lost_its_lease_ends_without_writing(conn: sqlite3.Connection, run_id: str,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The stage's own renew between units notices the takeover; advance returns the stage the winner left."""
    def searching(c: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str, token: str) -> str:
        c.execute("UPDATE deep_research SET advance_until='2000-01-01T00:00:00.000+00:00' WHERE id=?", (r["id"],))
        c.commit()
        winner = store.claim(c, r["id"])  # the lease expired mid-stage and another advance took the run
        assert winner
        store.move(c, r["id"], "task:winner", "searching", "awaiting_screening", token=winner)
        store.renew(c, r["id"], token)  # the slow advance's next unit
        return "unreachable"

    monkeypatch.setitem(deep.STAGES, "searching", searching)
    assert deep.advance(conn, None, run_id, "task:slow") == "awaiting_screening"
    assert conn.execute("SELECT advance_owner FROM deep_research WHERE id=?", (run_id,)).fetchone()[0] is not None
