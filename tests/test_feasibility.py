"""Topic feasibility check (ADR-181): status per dimension from recorded facts and verified answers only."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from researchos.api.routers import docs as docs_router
from researchos.services.container import Services
from researchos.services.writing import feasibility

from .fixtures.make_dataset import make
from .m2_helpers import new_plan, plan_content, run


def test_feasibility_from_recorded_facts(client: Any, conn: sqlite3.Connection, svc: Services, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docs_router, "gpu_info", lambda: {"detected": False})
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid_plan = new_plan(client, plan_content(client, ds))
    assert run(client, conn, svc, pid_plan)["state"] == "succeeded"
    pid = client.post("/api/v1/projects", json={"title": "Feasibility"}).json()["id"]
    body = {"topic": "motor imagery decoding", "dataset_ids": [ds["id"]], "template_id": "fault",
            "runs_planned": 5, "deadline_days": 7, "reference_plan": {"plan_id": pid_plan, "revision": 1}}
    r = client.post(f"/api/v1/projects/{pid}/feasibility", json=body, headers={"idempotency-key": "f-1"})
    assert r.status_code == 201, r.text
    out = r.json()
    status = {k: v["status"] for k, v in out["status"].items()}
    assert status == {"literature": "unknown", "data": "ok", "compute": "ok", "time": "ok"}, out["status"]
    assert out["facts"]["comparable_runs"] == 1 and out["facts"]["datasets"][0]["group_key"] == "subject"
    assert out["fields"]["prior"]["cited"] is False  # empty library: insufficient material, no invented prior work
    again = client.post(f"/api/v1/projects/{pid}/feasibility", json=body, headers={"idempotency-key": "f-1"})
    assert again.json()["doc"]["id"] == out["doc"]["id"]  # retried intent replays
    for s in client.get(f"/api/v1/docs/{out['doc']['id']}/suggestions", params={"status": "proposed"}).json():
        client.post(f"/api/v1/suggestions/{s['id']}/decision", json={"accept": True})
    doc = client.get(f"/api/v1/docs/{out['doc']['id']}").json()
    assert doc["kind"] == "feasibility" and doc["title"] == "Feasibility: motor imagery decoding"
    assert "| compute | ok |" in doc["body"] and "_(pending" not in doc["body"]
    assert "## Owner decision\n\n_(owner)_" in doc["body"]


def test_unknown_dataset_is_rejected(client: Any) -> None:
    pid = client.post("/api/v1/projects", json={"title": "F"}).json()["id"]
    r = client.post(f"/api/v1/projects/{pid}/feasibility", json={"topic": "x topic", "dataset_ids": ["ds_none"]},
                    headers={"idempotency-key": "f-2"})
    assert r.status_code == 404


def _facts(**over: Any) -> dict[str, Any]:
    f: dict[str, Any] = {
        "datasets": [{"name": "d", "version": "1", "license": "ODC-By", "egress": "cloud_ok", "group_key": "subject"}],
        "template_id": "t", "template": {"id": "t", "status": "reviewed", "resource_class": "cpu",
                                         "missing_modules": []},
        "gpu_detected": False, "comparable_runs": 3, "median_run_minutes": 10.0, "device": "cpu",
        "not_comparable_reason": None, "history": {"runs": 3, "median_minutes": 10.0}, "runs_planned": 50,
        "deadline_days": 1}
    f.update(over)
    return f


@pytest.mark.parametrize(("over", "dim", "expected"), [
    ({}, "time", "ok"),  # 500 min of 1440
    ({"runs_planned": 100}, "time", "at_risk"),  # 1000 > 720
    ({"runs_planned": 200}, "time", "blocked"),
    ({"median_run_minutes": None}, "time", "unknown"),
    ({"deadline_days": None}, "time", "unknown"),
    ({"datasets": []}, "data", "blocked"),
    ({"datasets": [{"name": "d", "version": "1", "license": "", "egress": "local_only", "group_key": None}]},
     "data", "at_risk"),
    ({"template": None}, "compute", "blocked"),
    ({"template_id": None, "template": None}, "compute", "unknown"),
    ({"template": {"id": "t", "status": "reviewed", "resource_class": "gpu", "missing_modules": []}},
     "compute", "blocked"),
    ({"template": {"id": "t", "status": "reviewed", "resource_class": "cpu", "missing_modules": ["mne"]}},
     "compute", "blocked"),
    ({"template": {"id": "t", "status": "unreviewed", "resource_class": "cpu", "missing_modules": []}},
     "compute", "at_risk"),
])
def test_status_rules(over: dict[str, Any], dim: str, expected: str) -> None:
    assert feasibility.assess(_facts(**over), {})[dim][0] == expected


def test_literature_needs_a_verified_cited_answer() -> None:
    assert feasibility.assess(_facts(), {"prior": {"cited": True}})["literature"][0] == "ok"
    assert feasibility.assess(_facts(), {"prior": {"cited": False}})["literature"][0] == "unknown"


def test_68_time_needs_a_comparable_workload(client: Any, conn: sqlite3.Connection, svc: Services, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Review #68: a run of the same template with another plan (other dataset/params) is history, not an estimate."""
    monkeypatch.setattr(docs_router, "gpu_info", lambda: {"detected": False})
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    other = new_plan(client, plan_content(client, ds))
    assert run(client, conn, svc, other)["state"] == "succeeded"
    ref = new_plan(client, plan_content(client, ds, params={"mode": "ok"}, hypothesis="the planned workload"))
    pid = client.post("/api/v1/projects", json={"title": "F68"}).json()["id"]
    body: dict[str, Any] = {"topic": "motor imagery decoding", "dataset_ids": [ds["id"]], "template_id": "fault",
                            "runs_planned": 5, "deadline_days": 7}
    cases = [(None, "no reference plan"), ({"plan_id": ref, "revision": 1}, "no succeeded run of the reference")]
    for i, (plan, reason) in enumerate(cases):
        r = client.post(f"/api/v1/projects/{pid}/feasibility", json={**body, "reference_plan": plan},
                        headers={"idempotency-key": f"f68-{i}"}).json()
        assert r["status"]["time"]["status"] == "unknown" and reason in r["status"]["time"]["reason"]
        assert r["facts"]["history"]["runs"] == 1 and r["facts"]["median_run_minutes"] is None
    r = client.post(f"/api/v1/projects/{pid}/feasibility", json={**body, "dataset_ids": [],
                    "reference_plan": {"plan_id": other, "revision": 1}}, headers={"idempotency-key": "f68-x"}).json()
    assert r["status"]["time"]["status"] == "unknown"  # the reference ran on a dataset that is not selected
