"""Next-experiment advisor (ADR-168): failure memory + constraints for every sampler, the gp_ei sampler."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from researchos.services.execution import bayes, sampling

from .conftest import Client
from .fixtures.make_dataset import make
from .m2_helpers import new_plan, plan_content

SPACE = {
    "lr": {"low": 1e-4, "high": 1.0, "log": True},
    "dropout": {"low": 0.0, "high": 0.5},
    "batch": {"low": 16, "high": 128, "int": True, "log": True},
}


def objective(p: dict[str, Any]) -> float:  # smooth, interior optimum at lr = 1e-2, dropout = 0.2
    return -((math.log10(p["lr"]) + 2) ** 2) - 4 * (p["dropout"] - 0.2) ** 2


def test_constraint_grammar_and_feasibility() -> None:
    sampling.check_rules(SPACE, ["batch <= 64", "lr > 0.001"])
    for bad in ("batch << 64", "depth <= 3", "batch <= many", "batch<=64"):
        with pytest.raises(ValueError, match="bad constraint"):
            sampling.check_rules(SPACE, [bad])
    p = {"lr": 0.5, "dropout": 0.1, "batch": 20}
    assert sampling.feasible(p, [], ["batch <= 64"]) and not sampling.feasible(p, ["lr > 0.3 and batch <= 24"], [])


@pytest.mark.parametrize("sampler", ["qmc", "tpe", "gp_ei"])
def test_every_sampler_respects_failure_memory_and_constraints(sampler: str) -> None:
    avoid, constraints = ["lr > 0.1 and batch <= 32"], ["batch <= 64", "dropout < 0.4"]
    hist = [(p, objective(p)) for p in sampling.qmc_batch(SPACE, {}, 8, seed=1, skip=0, avoid=[])]
    if sampler == "qmc":
        pts = sampling.qmc_batch(SPACE, {}, 6, seed=2, skip=0, avoid=avoid, constraints=constraints)
    elif sampler == "tpe":
        pts = sampling.tpe_batch(SPACE, {}, hist, 6, 3, "max", avoid, constraints)
    else:
        pts, _ = bayes.gp_ei_batch(SPACE, {}, hist, 6, 4, "max", avoid, constraints)
    assert len(pts) == 6 and all(sampling.feasible(p, avoid, constraints) for p in pts)


def test_gp_ei_is_deterministic_ranked_and_falls_back_without_history() -> None:
    hist = [(p, objective(p)) for p in sampling.qmc_batch(SPACE, {}, 10, seed=5, skip=0, avoid=[])]
    a, info = bayes.gp_ei_batch(SPACE, {}, hist, 4, 7, "max")
    b, _ = bayes.gp_ei_batch(SPACE, {}, hist, 4, 7, "max")
    eis = [d["ei"] for d in info["points"]]
    assert a == b and eis == sorted(eis, reverse=True) and len({tuple(sorted(p.items())) for p in a}) == 4
    assert info["model"] and info["max_ei"] == eis[0] and all(d["sd"] >= 0 for d in info["points"])
    few, why = bayes.gp_ei_batch(SPACE, {}, hist[:3], 4, 7, "max")
    assert len(few) == 4 and why["model"] is None and "fewer than 4" in why["reason"]


def test_gp_ei_beats_sobol_on_a_smooth_objective() -> None:
    """Same budget (8 start + 4 x 3 proposals), seeded: the model-guided search ends closer to the optimum."""
    start = sampling.qmc_batch(SPACE, {}, 8, seed=11, skip=0, avoid=[])
    hist = [(p, objective(p)) for p in start]
    for r in range(4):
        pts, _ = bayes.gp_ei_batch(SPACE, {}, hist, 3, 100 + r, "max")
        hist += [(p, objective(p)) for p in pts]
    sobol = start + sampling.qmc_batch(SPACE, {}, 12, seed=11, skip=8, avoid=[])
    assert max(v for _, v in hist) > max(objective(p) for p in sobol)


def test_study_protocol_rejects_unknown_samplers_and_bad_constraints(client: Client, tmp_path: Path) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds, template="planted", params={}, space=SPACE, metrics=["score"]))
    base = {"objective": "score", "factors": ["lr", "dropout"], "chance": 1 / 3}
    for proto, msg in (
        ({"search": {"sampler": "random", "max_trials": 5, "batch": 5, "patience_batches": 1}}, "search.sampler"),
        ({"constraints": ["batch <= lots"]}, "bad constraint"),
    ):
        r = client.post("/api/v1/studies", json={"plan_id": pid, "revision": 1, "protocol": {**base, **proto}})
        assert r.status_code == 422 and msg in r.json()["message"], r.text


def test_gp_ei_study_runs_end_to_end_within_constraints(client: Client, conn: Any, svc: Any, tmp_path: Path) -> None:
    from .m1_helpers import drain

    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(
        client,
        plan_content(
            client,
            ds,
            template="planted",
            params={},
            space=SPACE,
            metrics=["score"],
            limits={"max_minutes": 2, "stall_s": 30, "max_trials": 60},
        ),
    )
    proto = {
        "objective": "score",
        "factors": ["lr", "batch", "dropout"],
        "chance": 1 / 3,
        "constraints": ["batch <= 64"],
        "search": {"sampler": "gp_ei", "max_trials": 10, "batch": 5, "patience_batches": 2},
        "confirm": {"top": 2, "seeds": 3},
    }
    st = client.post("/api/v1/studies", json={"plan_id": pid, "revision": 1, "protocol": proto}).json()
    drain(conn, svc, max_tasks=200)
    runs = [t for t in client.get("/api/v1/tasks").json() if t["payload"].get("study_id") == st["id"]]
    search = [t for t in runs if str(t["payload"].get("tag", "")).startswith("search")]
    assert search and all(t["payload"]["params"]["batch"] <= 64 for t in runs)  # screen corners included
    assert client.get(f"/api/v1/plans/{pid}/revisions/2").json()["content"]["space"]["batch"]["high"] == 64
    assert client.get(f"/api/v1/studies/{st['id']}").json()["stage"] in ("awaiting_final_approval", "stopped")


def test_box_constraints_tighten_the_space() -> None:
    t = sampling.tighten(SPACE, ["batch < 64", "lr >= 0.001", "dropout <= 0.3"])
    assert (
        (t["batch"]["low"], t["batch"]["high"]) == (16, 63) and t["lr"]["low"] == 0.001 and t["dropout"]["high"] == 0.3
    )
    assert sampling.tighten(SPACE, ["batch > 31.5"])["batch"]["low"] == 32 and SPACE["batch"]["low"] == 16  # copy
    with pytest.raises(ValueError, match="leaves no values"):
        sampling.tighten(SPACE, ["batch <= 8"])


def test_uncertainty_decomposition_points_the_right_way() -> None:
    from researchos.services.execution.advisor import uncertainty

    def rows(spread: float, noise: float) -> list[dict[str, Any]]:
        return [{"lr": lr, "failed": False, "objective": lr * spread + s * noise} for lr in (1, 2, 3) for s in (-1, 1)]

    assert "add seeds" in uncertainty(rows(0.01, 1.0), ["lr"])["advice"]
    u = uncertainty(rows(1.0, 0.01), ["lr"])
    assert "exploring configurations" in u["advice"] and u["replicated_configurations"] == 3
    assert "not enough" in uncertainty([{"lr": 1, "failed": False, "objective": 1.0}], ["lr"])["advice"]


def test_advice_during_search_and_after(client: Client, conn: Any, svc: Any, tmp_path: Path) -> None:
    from .m1_helpers import drain

    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(
        client,
        plan_content(
            client,
            ds,
            template="planted",
            params={},
            space=SPACE,
            metrics=["score"],
            limits={"max_minutes": 2, "stall_s": 30, "max_trials": 60},
        ),
    )
    proto = {
        "objective": "score",
        "factors": ["lr", "batch", "dropout"],
        "chance": 1 / 3,
        "search": {"sampler": "gp_ei", "max_trials": 10, "batch": 5, "patience_batches": 2},
        "confirm": {"top": 2, "seeds": 3},
    }
    st = client.post("/api/v1/studies", json={"plan_id": pid, "revision": 1, "protocol": proto}).json()
    drain(conn, svc, max_tasks=20)  # the screen design: 8 corners + 2 centres, 2 seeds each
    a = client.get(f"/api/v1/studies/{st['id']}/advice").json()
    assert a["stage"] == "search" and a["recommendation"].startswith("continue")
    assert a["sensitivity"]["ranking"][0]["term"] == "lr"  # the planted main effect
    assert len(a["proposals"]) == 5 and all({"mean", "sd", "ei"} <= set(p) for p in a["proposals"])
    assert a["model"]["model"] and {r["rule"] for r in a["stop_rules"]} >= {
        "budget",
        "patience",
        "expected_improvement",
    }
    before = client.get("/api/v1/tasks").json()
    assert client.get(f"/api/v1/studies/{st['id']}/advice").json()["proposals"] == a["proposals"]  # deterministic
    assert client.get("/api/v1/tasks").json() == before  # advice launches nothing
    drain(conn, svc, max_tasks=200)
    done = client.get(f"/api/v1/studies/{st['id']}/advice").json()
    assert done["proposals"] == [] and "search is over" in done["recommendation"]
