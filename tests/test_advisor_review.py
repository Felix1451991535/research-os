"""Advisor review fixes (ADR-173, review #57-#61), each on the reported trigger."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from researchos.services.execution import bayes, sampling, search

from .conftest import Client
from .fixtures.make_dataset import make
from .m2_helpers import new_plan, plan_content


def test_57_open_float_bounds_and_contradictions() -> None:
    t = sampling.tighten({"x": {"low": 0.0, "high": 1.0}}, ["x < 0.5"])
    assert t["x"]["high"] < 0.5 and sampling.satisfies({"x": t["x"]["high"]}, "x < 0.5")
    with pytest.raises(ValueError, match="leaves no values"):
        sampling.tighten({"x": {"low": 0.0, "high": 1.0}}, ["x > 0.5", "x <= 0.5"])


def test_57_screen_corners_respect_strict_constraints(client: Client, tmp_path: Path) -> None:
    space = {"lr": {"low": 1e-4, "high": 1.0, "log": True}, "dropout": {"low": 0.0, "high": 0.5}}
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds, template="planted", params={}, space=space, metrics=["score"]))
    proto = {"objective": "score", "factors": ["lr", "dropout"], "chance": 1 / 3, "constraints": ["dropout < 0.5"]}
    st = client.post("/api/v1/studies", json={"plan_id": pid, "revision": 1, "protocol": proto}).json()
    runs = [t for t in client.get("/api/v1/tasks").json() if t["payload"].get("study_id") == st["id"]]
    assert runs and all(t["payload"]["params"]["dropout"] < 0.5 for t in runs)


def _history(space: dict[str, Any], f: Any, n: int = 6) -> list[tuple[dict[str, Any], float]]:
    return [(p, f(p)) for p in sampling.qmc_batch(space, {}, n, seed=1, skip=0, avoid=[])]


def test_58_fixed_log_dimension_is_left_out_of_the_model() -> None:
    space = {"lr": {"low": 0.1, "high": 0.1, "log": True}, "dropout": {"low": 0.0, "high": 0.5}}
    pts, info = bayes.gp_ei_batch(space, {}, _history(space, lambda p: -abs(p["dropout"] - 0.2)), 3, 2, "max")
    assert len(pts) == 3 and all(p["lr"] == 0.1 for p in pts) and info["model"]


def test_60_ei_ignores_irreducible_noise() -> None:
    """Replicated observations at -1 / +1 around the same mean: noise is not improvement uncertainty."""
    space = {"x": {"low": 0.0, "high": 1.0}}
    hist = [({"x": x}, d) for x in (0.1, 0.5, 0.9) for d in (-1.0, 1.0) for _ in range(2)]
    _, info = bayes.gp_ei_batch(space, {}, hist, 3, 4, "max")
    assert info["noise_sd"] > 0.5 and all(p["sd"] < info["noise_sd"] for p in info["points"])
    assert info["max_ei"] < 0.5 * info["noise_sd"]


def test_61_empty_feasible_pool_returns_a_reason() -> None:
    space = {"x": {"low": 0.0, "high": 1.0}, "y": {"low": 0.0, "high": 1.0}}
    hist = _history(space, lambda p: p["x"])
    pts, info = bayes.gp_ei_batch(space, {}, hist, 3, 5, "max", avoid=["x > -1.0"])
    assert pts == [] and "no feasible candidate" in info["reason"]


def test_59_failed_search_configurations_are_never_proposed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    space = {"x": {"low": 0, "high": 1, "int": True}}
    monkeypatch.setattr(search.plans, "get_revision", lambda conn, pid, rev: {"content": {"space": space}})
    results = [{"x": 0, "failed": False, "objective": 1.0}, {"x": 1, "failed": True, "objective": None}]
    for sampler in ("qmc", "tpe", "gp_ei"):
        st = {
            "plan_id": "p",
            "current_revision": 1,
            "data": {},
            "protocol": {
                "factors": ["x"],
                "direction": "max",
                "search": {"sampler": sampler, "batch": 2, "max_trials": 10, "patience_batches": 2},
            },
        }
        pts, info = search.propose(None, st, 0, results)  # type: ignore[arg-type]
        assert all(p["x"] != 1 for p in pts) and info["failed_configurations"] == 1, sampler


@pytest.mark.parametrize("sampler", ["qmc", "tpe", "gp_ei"])
def test_65_unconditional_failure_region_stops_with_a_reason(sampler: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex repro #65: four failures and one success at the same point give the root rule "always"."""
    space = {"x": {"low": 0.0, "high": 1.0}}
    monkeypatch.setattr(search.plans, "get_revision", lambda conn, pid, rev: {"content": {"space": space}})
    st = {
        "plan_id": "p",
        "current_revision": 1,
        "data": {},
        "protocol": {"factors": ["x"], "direction": "max", "search": {"sampler": sampler, "batch": 2}},
    }
    results = [{"x": 0.5, "failed": True, "objective": None} for _ in range(4)]
    results.append({"x": 0.5, "failed": False, "objective": 1.0})
    assert search.memory(st, results, space)[0] == ["always"]
    pts, info = search.propose(None, st, 1, results)  # type: ignore[arg-type]
    assert pts == [] and "failed wherever they were tried" in info["reason"]


def test_60_incumbent_is_denoised() -> None:
    space = {"x": {"low": 0.0, "high": 1.0}}
    hist = [({"x": x}, d) for x in (0.1, 0.5, 0.9) for d in (-1.0, 1.0) for _ in range(2)]
    _, info = bayes.gp_ei_batch(space, {}, hist, 3, 4, "max")
    assert info["incumbent"] < 0.5 < max(v for _, v in hist)  # not the luckiest noisy observation (1.0)


def _planted_study(client: Client, tmp_path: Path, sampler: str = "qmc") -> tuple[str, str]:
    space = {
        "lr": {"low": 1e-4, "high": 1.0, "log": True},
        "batch": {"low": 16, "high": 128, "int": True, "log": True},
        "dropout": {"low": 0.0, "high": 0.5},
    }
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(
        client,
        plan_content(
            client,
            ds,
            template="planted",
            params={},
            space=space,
            metrics=["score"],
            limits={"max_minutes": 2, "stall_s": 30, "max_trials": 60},
        ),
    )
    proto = {
        "objective": "score",
        "factors": ["lr", "batch", "dropout"],
        "chance": 1 / 3,
        "search": {"sampler": sampler, "max_trials": 15, "batch": 5, "patience_batches": 3},
        "confirm": {"top": 2, "seeds": 2},
    }
    return pid, client.post("/api/v1/studies", json={"plan_id": pid, "revision": 1, "protocol": proto}).json()["id"]


def test_59_study_never_reproposes_failed_configurations(client: Client, conn: Any, svc: Any, tmp_path: Path) -> None:
    from researchos.services.execution.study_data import config_key

    from .m1_helpers import drain

    _, sid = _planted_study(client, tmp_path)
    drain(conn, svc, max_tasks=200)
    tasks = sorted(
        (t for t in client.get("/api/v1/tasks").json() if t["payload"].get("study_id") == sid),
        key=lambda t: t["created_at"],
    )
    keys = ["lr", "batch", "dropout"]
    failed_before: set[str] = set()
    searched = 0
    for t in tasks:
        key = config_key(t["payload"]["params"], keys)
        if str(t["payload"].get("tag", "")).startswith("search"):
            searched += 1
            assert key not in failed_before
        if t["state"] != "succeeded":
            failed_before.add(key)
    assert failed_before and searched  # the planted NaN region produced failures before the search ran


def test_61_study_stops_searching_when_no_candidate_is_feasible(
    client: Client, conn: Any, svc: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .m1_helpers import drain

    monkeypatch.setattr(search, "propose", lambda *a, **k: ([], {"reason": "no feasible candidate left (test)"}))
    _, sid = _planted_study(client, tmp_path)
    drain(conn, svc, max_tasks=200)
    st = client.get(f"/api/v1/studies/{sid}").json()
    assert (
        st["data"]["search_stopped"] == "no feasible candidate left (test)" and st["data"].get("search_batches", 0) == 0
    )
    assert st["stage"] in ("awaiting_final_approval", "stopped")  # moved on to confirmation instead of crashing
