"""M0 K2/K3 gates: double submit -> one task; state machine; approvals bound to hash; cancel; fencing; reaping."""
from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from researchos.contracts.tasks import TRANSITIONS, TaskOutcome, TaskState, can_transition
from researchos.services.container import Services
from researchos.services.orchestration import leases, workers

from .conftest import PASSWORD, Client


def _task(client: Client, kind: str = "system.sleep", seconds: float = 0, key: str | None = None,
          **extra: Any) -> Any:
    h = {"idempotency-key": key} if key else {}
    return client.post("/api/v1/tasks", json={"kind": kind, "payload": {"seconds": seconds, **extra}}, headers=h)


def test_double_submit_one_task(client: Client) -> None:
    a = _task(client, key="click-1")
    b = _task(client, key="click-1")
    assert a.status_code == 202 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert len(client.get("/api/v1/tasks").json()) == 1
    assert _task(client, key="click-1", seconds=5).json()["code"] == "idempotency_mismatch"


def test_payload_validated_and_unknown_kind(client: Client) -> None:
    assert client.post("/api/v1/tasks", json={"kind": "nope"}).json()["code"] == "unknown_task_kind"
    assert client.post("/api/v1/tasks", json={"kind": "system.sleep", "payload": {"seconds": -1}}
                       ).json()["code"] == "invalid_payload"


def test_transition_table_never_lets_terminal_move() -> None:
    for s in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED):
        assert TRANSITIONS[s] == frozenset()
    assert not can_transition(TaskState.QUEUED, TaskState.SUCCEEDED)


def test_worker_runs_task(client: Client, conn: sqlite3.Connection, svc: Services) -> None:
    tid = _task(client, seconds=0.2).json()["id"]
    wid = workers.register(conn, 1, "t")
    c = leases.claim(conn, wid, 30, svc.kinds)
    assert c and c.task["id"] == tid and c.generation == 1
    assert leases.renew(conn, tid, wid, 1, 30, "x", {"a": 1}) == "ok"
    assert leases.finish(conn, c, wid, TaskOutcome(TaskState.SUCCEEDED, {"ok": 1}))
    t = client.get(f"/api/v1/tasks/{tid}").json()
    assert t["state"] == "succeeded" and t["result"] == {"ok": 1}
    kinds = [e["kind"] for e in client.get("/api/v1/events", params={"entity_id": tid}).json()]
    assert kinds == ["task.queued", "task.running", "task.succeeded"]


def test_stale_worker_is_fenced(client: Client, conn: sqlite3.Connection, svc: Services) -> None:
    tid = _task(client, seconds=1).json()["id"]
    old = workers.register(conn, 1, "t")
    c1 = leases.claim(conn, old, 0.01, svc.kinds)
    assert c1
    conn.execute("UPDATE workers SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (old,))
    time.sleep(0.05)  # let the 10 ms lease expire
    new = workers.register(conn, 2, "t")
    assert leases.reap(conn, new, svc.kinds, 1.0) == [tid]
    assert client.get(f"/api/v1/tasks/{tid}").json()["state"] == "interrupted"  # non-retryable: never auto-rerun
    assert leases.renew(conn, tid, old, c1.generation, 30) == "fenced"
    assert not leases.finish(conn, c1, old, TaskOutcome(TaskState.SUCCEEDED, {"late": True}))
    assert client.get(f"/api/v1/tasks/{tid}").json()["result"] is None
    client.post(f"/api/v1/tasks/{tid}/retry")
    c2 = leases.claim(conn, new, 30, svc.kinds)
    assert c2 and c2.generation == c1.generation + 1


def test_retryable_kind_requeues_with_backoff(client: Client, conn: sqlite3.Connection, svc: Services) -> None:
    tid = _task(client, kind="system.sleep_retryable", seconds=1).json()["id"]
    old = workers.register(conn, 1, "t")
    assert leases.claim(conn, old, 0.01, svc.kinds)
    workers.stop(conn, old)
    time.sleep(0.05)
    assert leases.reap(conn, workers.register(conn, 2, "t"), svc.kinds, 1.0) == [tid]
    t = client.get(f"/api/v1/tasks/{tid}").json()
    assert t["state"] == "queued" and t["attempt"] == 1


def test_gpu_class_is_serial(client: Client, conn: sqlite3.Connection, svc: Services) -> None:
    _task(client, kind="system.sleep_gpu", seconds=1)
    _task(client, kind="system.sleep_gpu", seconds=1)
    wid = workers.register(conn, 1, "t")
    assert leases.claim(conn, wid, 30, svc.kinds) is not None
    assert leases.claim(conn, wid, 30, svc.kinds) is None


def test_cancel_queued_and_running(client: Client, conn: sqlite3.Connection, svc: Services) -> None:
    q = _task(client, seconds=1).json()["id"]
    assert client.post(f"/api/v1/tasks/{q}/cancel").json()["state"] == "cancelled"
    r = _task(client, seconds=1).json()["id"]
    wid = workers.register(conn, 1, "t")
    c = leases.claim(conn, wid, 30, svc.kinds)
    assert c
    assert client.post(f"/api/v1/tasks/{r}/cancel").json()["state"] == "cancelling"
    assert leases.renew(conn, r, wid, c.generation, 30) == "cancel"
    leases.finish(conn, c, wid, TaskOutcome(TaskState.CANCELLED))
    assert client.get(f"/api/v1/tasks/{r}").json()["state"] == "cancelled"


def test_approval_gate(client: Client, conn: sqlite3.Connection, svc: Services) -> None:
    t = _task(client, kind="system.guarded", seconds=0).json()
    assert t["state"] == "awaiting_approval"
    wid = workers.register(conn, 1, "t")
    assert leases.claim(conn, wid, 30, svc.kinds) is None
    h = client.get("/api/v1/approvals/subject-hash", params={"subject_type": "task", "subject_id": t["id"]}).json()
    bad = client.post("/api/v1/approvals", json={"subject_type": "task", "subject_id": t["id"], "expected_hash": "x"})
    assert bad.status_code == 409 and bad.json()["code"] == "subject_changed"
    a = client.post("/api/v1/approvals", json={"subject_type": "task", "subject_id": t["id"],
                                               "expected_hash": h["hash"]})
    assert a.status_code == 201
    assert client.get(f"/api/v1/tasks/{t['id']}").json()["state"] == "queued"
    client.post(f"/api/v1/approvals/{a.json()['id']}/revoke")
    assert client.get(f"/api/v1/tasks/{t['id']}").json()["state"] == "blocked"


def test_remote_approval_needs_reauth(client: Client, app: Any) -> None:
    code = client.post("/api/v1/auth/pairing-codes", json={}).json()["code"]
    ipad = Client(app, client=("192.168.1.30", 4000))
    ipad.post("/api/v1/auth/pair", json={"code": code})
    body = {"subject_type": "external_publish", "subject_id": "x"}
    # external_publish has no resolver yet -> 422 only after the re-auth gate passes
    assert ipad.post("/api/v1/approvals", json=body).json()["code"] == "reauth_required"
    ipad.post("/api/v1/auth/reauth", json={"password": PASSWORD})
    assert ipad.post("/api/v1/approvals", json=body).json()["code"] == "subject_not_resolvable"


@pytest.mark.parametrize("state", ["succeeded", "failed"])
def test_illegal_api_transition(client: Client, conn: sqlite3.Connection, svc: Services, state: str) -> None:
    tid = _task(client, seconds=0).json()["id"]
    wid = workers.register(conn, 1, "t")
    c = leases.claim(conn, wid, 30, svc.kinds)
    assert c
    leases.finish(conn, c, wid, TaskOutcome(TaskState(state)))
    assert client.post(f"/api/v1/tasks/{tid}/retry").json()["code"] == "not_retryable"
