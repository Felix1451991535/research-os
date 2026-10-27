"""Paired run comparison (ADR-191): McNemar table from saved predictions, statistics from the diag_stats tools."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from researchos.services.container import Services

from .fixtures.make_dataset import make
from .m2_helpers import new_plan, plan_content, run


@pytest.fixture
def stats_tools(svc: Services) -> None:
    """The host registers diag_stats tools from the plugin; the unit services here load no plugins."""
    paired = pytest.importorskip("researchos_plugin_diag_stats.paired")
    power = pytest.importorskip("researchos_plugin_diag_stats.power")
    for b in (paired.BINDING, power.BINDING):
        svc.tools.register(b.spec, lambda args, b=b: b.impl(args, None))


def _preds(settings: Any, run_id: str) -> Path:
    return Path(settings.data_dir) / "runs" / run_id / "predictions" / "search_val.csv"


def _rewrite(path: Path, flip: int, *, label_shift: bool = False) -> int:
    """Make the first `flip` windows wrong (a known number of discordant pairs). Returns the window count."""
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    for i, r in enumerate(rows):
        if label_shift and i == 0:
            r["y_true"] = r["y_true"] + "x"
        if i < flip:
            r["y_pred"] = "wrong"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["sample_id", "y_true", "y_pred"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def test_paired_comparison_counts_and_statistics(client: Any, conn: sqlite3.Connection, svc: Services,
                                                 settings: Any, tmp_path: Path, stats_tools: None) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    a = run(client, conn, svc, pid, seed=0)["run"]["id"]
    b = run(client, conn, svc, pid, seed=0)["run"]["id"]
    with _preds(settings, a).open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    correct_a = sum(r["y_true"] == r["y_pred"] for r in rows)
    flipped = sum(r["y_true"] == r["y_pred"] for r in rows[:5])  # A-correct windows B now gets wrong
    n = _rewrite(_preds(settings, b), 5)
    r = client.post(f"/api/v1/runs/{a}/paired", json={"other_run_id": b})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["windows"] == n and out["table"][0][1] == flipped and out["table"][1][0] == 0
    assert out["accuracy_a"] == correct_a / n and out["difference_b_minus_a"] == -flipped / n
    lo, hi = out["difference_ci95"]
    assert lo <= out["difference_b_minus_a"] <= hi
    assert out["mcnemar"] and out["mcnemar_reference"].startswith("McNemar")
    assert out["minimum_detectable_h_80"] > 0 and "not independent" in out["caveat"]


def test_incomparable_runs_are_refused(client: Any, conn: sqlite3.Connection, svc: Services, settings: Any,
                                      tmp_path: Path) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    a = run(client, conn, svc, pid, seed=0)["run"]["id"]
    b = run(client, conn, svc, pid, seed=0)["run"]["id"]
    other_seed = run(client, conn, svc, pid, seed=99)["run"]["id"]
    r71 = client.post(f"/api/v1/runs/{a}/paired", json={"other_run_id": other_seed}).json()
    assert r71["code"] == "not_comparable" and "seed" in r71["message"]  # review #71
    assert client.post(f"/api/v1/runs/{a}/paired", json={"other_run_id": b}).json()["code"] == "missing_tool"
    _rewrite(_preds(settings, b), 0, label_shift=True)
    assert client.post(f"/api/v1/runs/{a}/paired", json={"other_run_id": b}).json()["code"] == "not_comparable"
    crashed = run(client, conn, svc, pid, params={"mode": "crash"})["run"]["id"]
    assert client.post(f"/api/v1/runs/{a}/paired", json={"other_run_id": crashed}).json()["code"] == \
        "run_not_succeeded"
    assert client.post(f"/api/v1/runs/{a}/paired", json={"other_run_id": "nope"}).status_code == 404


def test_69_fewer_than_two_windows_is_a_clear_refusal(client: Any, conn: sqlite3.Connection, svc: Services,
                                                     settings: Any, tmp_path: Path, stats_tools: None) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    a, b = (run(client, conn, svc, pid, seed=0)["run"]["id"] for _ in range(2))
    for keep in (0, 1):
        for rid in (a, b):
            path = _preds(settings, rid)
            lines = path.read_text().splitlines()
            path.write_text("\n".join(lines[: 1 + keep]) + "\n")
        r = client.post(f"/api/v1/runs/{a}/paired", json={"other_run_id": b})
        assert r.status_code == 422 and r.json()["code"] == "too_few_windows", r.text


def test_70_interval_stays_informative_at_the_boundaries() -> None:
    from researchos.services.execution.paired import newcombe_paired

    lo, hi = newcombe_paired(0, 0, 2, 0)  # Codex repro: A right twice, B wrong twice (Wald gave [-1, -1])
    assert lo == -1.0 and -1.0 < hi < 0
    lo, hi = newcombe_paired(2, 0, 0, 0)  # perfect agreement (Wald gave [0, 0])
    assert lo < 0 < hi
    lo, hi = newcombe_paired(0, 0, 1, 1)  # one discordance (Wald went below -1)
    assert -1.0 <= lo < -0.5 < hi <= 1.0
    for cells in ((40, 25, 10, 25), (300, 40, 20, 140)):  # large n: close to the Wald interval
        both, b, a, d = cells
        n = sum(cells)
        diff = (b - a) / n
        se = ((a + b) - (b - a) ** 2 / n) ** 0.5 / n
        lo, hi = newcombe_paired(*cells)
        assert lo < diff < hi and abs(lo - (diff - 1.96 * se)) < 0.02 and abs(hi - (diff + 1.96 * se)) < 0.02


def test_manual_negative_control_run(client: Any, conn: sqlite3.Connection, svc: Services, settings: Any,
                                     tmp_path: Path) -> None:
    """ADR-195: the shuffled-label control is available outside a study, at the trial stage only."""
    import json
    import uuid

    from .m1_helpers import drain

    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    body = {"stage": "trial", "params": {"mode": "ok"}, "seed": 0, "control": "shuffled_labels"}
    r = client.post(f"/api/v1/plans/{pid}/revisions/1/runs", json=body, headers={"idempotency-key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    drain(conn, svc, max_tasks=1)
    run_id = client.get(f"/api/v1/tasks/{r.json()['id']}/runs").json()[-1]["id"]
    manifest = json.loads((Path(settings.data_dir) / "runs" / run_id / "run.json").read_text())
    assert manifest["control"] == "shuffled_labels"
    bad = client.post(f"/api/v1/plans/{pid}/revisions/1/runs", json={**body, "stage": "confirm"},
                      headers={"idempotency-key": uuid.uuid4().hex})
    assert bad.status_code == 422 and bad.json()["code"] == "bad_control"


def test_74_control_outside_trial_is_refused_on_every_route(client: Any, conn: sqlite3.Connection, settings: Any,
                                                           tmp_path: Path) -> None:
    from types import SimpleNamespace

    from researchos.services.execution import run_task

    payload = {"plan_id": "p", "revision": 1, "stage": "final_eval", "params": {}, "seed": 0,
               "control": "shuffled_labels"}  # Codex minimal repro
    r = client.post("/api/v1/tasks", json={"kind": "experiment.run", "payload": payload},
                    headers={"idempotency-key": "c74"})
    assert r.status_code == 422, r.text
    out = run_task._run(conn, SimpleNamespace(payload=payload), settings, None)  # type: ignore[arg-type]
    assert out.error_code == "bad_control"
