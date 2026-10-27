"""Pre-registered ablation stage (ADR-185): one factor at a time back to its reference, paired on confirm seeds."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from researchos.services.container import Services
from researchos.services.execution import ablation

from .conftest import Client
from .m1_helpers import drain
from .test_m4_study import PROTOCOL, SPACE


def _study(client: Client, conn: sqlite3.Connection, svc: Services, tmp_path: Path, proto: dict[str, Any]) -> Any:
    from .fixtures.make_dataset import make
    from .m2_helpers import new_plan, plan_content

    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds, template="planted", params={}, space=SPACE, metrics=["score"],
                                        limits={"max_minutes": 2, "stall_s": 30, "max_trials": 80}))
    return client.post("/api/v1/studies", json={"plan_id": pid, "revision": 1, "protocol": proto})


def test_ablation_attributes_the_planted_effect(client: Client, conn: sqlite3.Connection, svc: Services,
                                                tmp_path: Path) -> None:
    proto = {**PROTOCOL, "ablation": {"reference": {"lr": 0.0001}}}
    sid = _study(client, conn, svc, tmp_path, proto).json()["id"]
    drain(conn, svc, max_tasks=250)
    st = client.get(f"/api/v1/studies/{sid}").json()
    assert st["stage"] == "awaiting_final_approval", st["data"].get("stop_reason")
    abl = st["data"]["ablation"]
    res = {r["factor"]: r for r in abl["results"]}
    assert res["lr"]["verdict"].startswith("contributes") and res["lr"]["n"] == 5  # planted: lr=1e-4 is far worse
    assert res["lr"]["ci95"][0] > 0 and res["lr"]["reference"] == 0.0001
    skipped = {s["factor"]: s["reason"] for s in abl["plan"]["skipped"]}
    assert "dropout" in skipped  # narrowed to one value by the screen: nothing to ablate
    tasks = [t for t in client.get("/api/v1/tasks").json() if t["payload"].get("study_id") == sid]
    abl_tasks = [t for t in tasks if t["payload"]["tag"] == "ablation"]
    assert abl_tasks and {t["payload"]["stage"] for t in abl_tasks} == {"confirm"}  # validation data only
    assert {t["payload"]["seed"] for t in abl_tasks} == set(range(100, 105))  # paired with the confirm seeds
    assert not [t for t in tasks if t["payload"]["stage"] == "final_eval"]  # test set untouched
    assert client.get(f"/api/v1/studies/{sid}/advice").json()["ablation"]["results"]


def test_study_without_ablation_skips_the_stage(client: Client, conn: sqlite3.Connection, svc: Services,
                                                tmp_path: Path) -> None:
    sid = _study(client, conn, svc, tmp_path, PROTOCOL).json()["id"]
    drain(conn, svc, max_tasks=200)
    st = client.get(f"/api/v1/studies/{sid}").json()
    assert st["stage"] == "awaiting_final_approval" and "ablation" not in st["data"]


@pytest.mark.parametrize("abl", [{"reference": {"momentum": 0.9}}, {"reference": {"lr": 5.0}}, {"other": 1}, []])
def test_bad_ablation_protocol_rejected(client: Client, conn: sqlite3.Connection, svc: Services, tmp_path: Path,
                                        abl: Any) -> None:
    r = _study(client, conn, svc, tmp_path, {**PROTOCOL, "ablation": abl})
    assert r.status_code == 422 and r.json()["code"] == "bad_protocol", r.text


def test_contribution_interval_and_direction() -> None:
    frozen = {100: 0.9, 101: 0.91, 102: 0.89}
    worse = {100: 0.8, 101: 0.82, 102: 0.79}
    assert ablation.contribution(frozen, worse, "max")["verdict"].startswith("contributes")
    assert ablation.contribution(frozen, worse, "min")["verdict"].startswith("removing it helps")
    same = {100: 0.91, 101: 0.89, 102: 0.9}
    assert "includes 0" in ablation.contribution(frozen, same, "max")["verdict"]
    one = ablation.contribution(frozen, {100: 0.5}, "max")
    assert one["verdict"] == "insufficient paired runs" and one["ci95"] is None
