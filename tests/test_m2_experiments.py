"""M2 gate: controlled experiments with real template subprocesses. Fault injection (ARCHITECTURE §6.8),
approval/plan-revision rules, final_eval once, template hash change, cancel, reports."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from researchos.kernel.config import Settings
from researchos.services.container import Services

from .conftest import Client
from .fixtures.make_dataset import make
from .m2_helpers import approve, detectors_fired, new_plan, plan_content, run


@pytest.fixture
def ds(client: Client, tmp_path: Path) -> dict[str, Any]:
    r = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "har_syn"))})
    assert r.status_code == 201, r.text
    return r.json()


def test_ok_run_end_to_end(
    client: Client, conn: sqlite3.Connection, svc: Services, ds: dict, settings: Settings
) -> None:
    pid = new_plan(client, plan_content(client, ds))
    t = run(client, conn, svc, pid, seed=7)
    assert t["state"] == "succeeded", (t["error_code"], t["error_message"])
    r = t["run"]
    m = {x["name"]: x for x in r["metrics"]}
    assert m["macro_f1"]["verification"] == "recomputed" and 0.5 < m["macro_f1"]["value"] <= 1
    run_dir = settings.data_dir / r["run_dir"]
    assert (run_dir / "inputs" / "train.npz").is_file() and not (run_dir / "inputs" / "test.npz").exists()
    kinds = {a["kind"] for a in r["artifacts"]}
    assert {"manifest", "metrics", "predictions"} <= kinds
    import json

    man = json.loads((run_dir / "run.json").read_text())
    assert man["seed"] == 7 and man["template"]["hash"] and man["split_hash"] and "commit" in man["git"]
    rep = client.post(f"/api/v1/runs/{r['id']}/report").json()
    assert rep["missing_numbers"] == [] and "(recomputed)" in Path(rep["markdown"]).read_text()


@pytest.mark.parametrize(
    "mode,detector",
    [
        ("nan", "nonfinite"),
        ("diverge", "divergence"),
        ("hang", "stall"),
        ("misreport", "metric_recompute"),
        ("read_test", "sample_provenance"),
        ("resplit", "sample_provenance"),
        ("crash", "exit_solver"),
    ],
)
def test_fault_injection(
    mode: str, detector: str, client: Client, conn: sqlite3.Connection, svc: Services, ds: dict
) -> None:
    pid = new_plan(client, plan_content(client, ds, params={"mode": mode}))
    t = run(client, conn, svc, pid)
    assert t["state"] == "failed" and detector in detectors_fired(t), (t["error_code"], detectors_fired(t))


def test_random_window_split_leak_detected(client: Client, conn: sqlite3.Connection, svc: Services, ds: dict) -> None:
    pid = new_plan(client, plan_content(client, ds, split={"kind": "random_rows", "seed": 3, "train_fraction": 0.8}))
    t = run(client, conn, svc, pid)
    assert t["state"] == "failed" and "split_integrity" in detectors_fired(t)
    assert any(s.get("value", {}).get("shared_half_hashes") for s in t["run"]["signals"])


def test_misreport_keeps_recomputed_value(client: Client, conn: sqlite3.Connection, svc: Services, ds: dict) -> None:
    t = run(client, conn, svc, new_plan(client, plan_content(client, ds, params={"mode": "misreport"})))
    m = t["run"]["metrics"][0]
    assert m["verification"] == "failed" and abs(m["reported_value"] - m["value"] - 0.06) < 1e-9


def test_unapproved_plan_refused(client: Client, ds: dict) -> None:
    pid = new_plan(client, plan_content(client, ds), approved=False)
    r = client.post(f"/api/v1/plans/{pid}/revisions/1/runs", json={"stage": "trial", "params": {"mode": "ok"}})
    assert r.status_code == 409 and r.json()["code"] == "plan_not_approved"


def test_narrowing_inherits_widening_needs_approval(
    client: Client, conn: sqlite3.Connection, svc: Services, ds: dict
) -> None:
    base = plan_content(client, ds, params={"mode": "ok"}, space={"lr": {"low": 0.001, "high": 0.1}})
    pid = new_plan(client, base)
    narrow = client.post(
        f"/api/v1/plans/{pid}/revisions", json={"content": {**base, "space": {"lr": {"low": 0.01, "high": 0.05}}}}
    ).json()
    assert narrow["change_kind"] == "narrowing"
    t = run(client, conn, svc, pid, rev=2, params={"mode": "ok", "lr": 0.02})
    assert t["state"] == "succeeded"
    t = run(client, conn, svc, pid, rev=2, params={"mode": "ok", "lr": 0.09})
    assert t["state"] == "blocked" and t["error_code"] == "params_not_approved"
    wide = client.post(
        f"/api/v1/plans/{pid}/revisions", json={"content": {**base, "space": {"lr": {"low": 0.001, "high": 1.0}}}}
    ).json()
    assert wide["change_kind"] == "other"
    r = client.post(f"/api/v1/plans/{pid}/revisions/3/runs", json={"stage": "trial", "params": {"mode": "ok"}})
    assert r.json()["code"] == "plan_not_approved"


def test_plan_revisions_immutable(conn: sqlite3.Connection, client: Client, ds: dict) -> None:
    pid = new_plan(client, plan_content(client, ds))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE plan_revisions SET content='{}' WHERE plan_id=?", (pid,))


def test_final_eval_needs_own_approval_and_runs_once(
    client: Client, conn: sqlite3.Connection, svc: Services, ds: dict, settings: Settings
) -> None:
    pid = new_plan(client, plan_content(client, ds))
    r = client.post(f"/api/v1/plans/{pid}/revisions/1/runs", json={"stage": "final_eval", "params": {"mode": "ok"}})
    assert r.json()["code"] == "final_eval_not_approved"
    approve(client, "final_eval", f"{pid}@1")
    # the fault template reads search_val; final_eval exposes train+test only -> template fails honestly
    t = run(client, conn, svc, pid, stage="final_eval")
    run_dir = settings.data_dir / t["run"]["run_dir"]
    assert (run_dir / "inputs" / "test.npz").is_file() and not (run_dir / "inputs" / "search_val.npz").exists()


def test_final_eval_second_time_refused(client: Client, conn: sqlite3.Connection, svc: Services, ds: dict) -> None:
    pid = new_plan(
        client,
        plan_content(
            client,
            ds,
            template="har_baselines",
            params={"rf_trees": 20},
            metrics=["rf.macro_f1"],
            limits={"max_minutes": 5, "stall_s": 60},
        ),
    )
    approve(client, "final_eval", f"{pid}@1")
    first = run(client, conn, svc, pid, stage="final_eval")
    assert first["state"] == "succeeded", (first["error_code"], first["error_message"])
    eff = {(m["name"], m["split"]): m for m in first["run"]["metrics"]}
    lat = eff[("rf.latency_b1_p50", "device:cpu")]
    assert lat["unit"] == "ms" and lat["value"] > 0 and lat["verification"] == "self_reported"
    assert eff[("lr.model_bytes", "device:cpu")]["value"] > 0
    r = client.post(f"/api/v1/plans/{pid}/revisions/1/runs", json={"stage": "final_eval", "params": {"rf_trees": 20}})
    assert r.json()["code"] == "final_eval_already_done"


def test_template_hash_change_blocks(
    client: Client, conn: sqlite3.Connection, svc: Services, ds: dict, tmp_path: Path, settings: Settings
) -> None:
    import shutil

    root = tmp_path / "tpl"
    shutil.copytree(Path(settings.extra_template_dirs[0]), root)
    settings.extra_template_dirs = [str(root)]
    from researchos.services.wiring import build_services

    svc2 = build_services(settings, load_plugins=False, providers={})
    client.app.state.services = svc2
    pid = new_plan(client, plan_content(client, ds))
    (root / "fault" / "run.py").write_text((root / "fault" / "run.py").read_text() + "\n# edited after review\n")
    t = run(client, conn, svc2, pid)
    assert t["state"] == "blocked" and t["error_code"] == "template_not_reviewed"


def test_cancel_running_experiment(client: Client, conn: sqlite3.Connection, svc: Services, ds: dict) -> None:
    import threading
    import time

    from .m1_helpers import drain

    pid = new_plan(client, plan_content(client, ds, params={"mode": "slow"}, limits={"max_minutes": 2, "stall_s": 30}))
    tid = client.post(
        f"/api/v1/plans/{pid}/revisions/1/runs", json={"stage": "trial", "params": {"mode": "slow"}}
    ).json()["id"]
    th = threading.Thread(target=drain, args=(conn, svc, 1))
    th.start()
    for _ in range(100):
        if client.get(f"/api/v1/tasks/{tid}").json()["state"] == "running":
            break
        time.sleep(0.1)
    time.sleep(1.0)
    client.post(f"/api/v1/tasks/{tid}/cancel")
    th.join(timeout=60)
    assert client.get(f"/api/v1/tasks/{tid}").json()["state"] == "cancelled"


def test_smoke_template_pure_python(client: Client, conn: sqlite3.Connection, svc: Services) -> None:
    pid = new_plan(client, plan_content(client, None, template="smoke_cpu", params={"n": 200, "steps": 3}))
    t = run(client, conn, svc, pid, stage="smoke")
    assert t["state"] == "succeeded", (t["error_code"], t["error_message"])
    assert t["run"]["metrics"][0]["verification"] == "recomputed"


def test_missing_dependency_is_honest(client: Client, conn: sqlite3.Connection, svc: Services, ds: dict) -> None:
    pid = new_plan(
        client,
        plan_content(client, ds, template="har_cnn1d", params={"epochs": 1}, limits={"max_minutes": 5, "stall_s": 60}),
    )
    t = run(client, conn, svc, pid)
    try:
        import torch  # noqa: F401
    except ImportError:
        assert t["state"] == "failed" and t["error_code"] == "missing_dependency"
    else:
        assert t["state"] == "succeeded", (t["error_code"], t["error_message"])
        m = {(x["name"], x["split"]): x for x in t["run"]["metrics"]}
        assert m[("cnn.latency_b1_p50", "device:cpu")]["unit"] == "ms" and m[("cnn.params", "device:cpu")]["value"] > 0
