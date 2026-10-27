"""Pre-registered ablation of the frozen configuration (ADR-185; idea -> experiment -> ablation, owner design
reference AI-Scientist-v3 - ideas only, no code taken). Opt-in via the protocol at study creation:
`"ablation": {"reference": {factor: value}}` (default reference = the screen centre of each factor).

After the shuffled-label control passes, each factor of the frozen configuration is reset to its reference value,
one at a time, and run on the confirm seeds (validation data, never the test set). The contribution of a factor is
the paired difference frozen - ablated per seed (sign flipped for minimisation), reported with its mean, SD, n and a
95% t interval; the verdict says only what the interval supports. Factors already at their reference, or whose
ablated configuration violates the protocol constraints, are listed as skipped with the reason."""

from __future__ import annotations

import sqlite3
from statistics import mean, stdev
from typing import Any

from ..orchestration.registry import KindRegistry
from . import sampling
from . import study_data as sd

TAG = "ablation"


def _inside(spec: dict[str, Any], v: Any) -> bool:
    return bool(v in spec["choices"]) if "choices" in spec else bool(spec["low"] <= v <= spec["high"])


def validate(proto: dict[str, Any], space: dict[str, Any]) -> None:
    abl = proto.get("ablation")
    if abl is None:
        return
    ref = abl.get("reference", {}) if isinstance(abl, dict) else None
    if not isinstance(ref, dict) or set(abl) - {"reference"}:
        raise ValueError('ablation must be {"reference": {factor: value}} (reference optional)')
    for f, v in ref.items():
        if f not in proto["factors"]:
            raise ValueError(f"ablation reference {f!r} is not a study factor")
        if not _inside(space[f], v):
            raise ValueError(f"ablation reference {f}={v!r} is outside the approved space")


def plan(proto: dict[str, Any], space: dict[str, Any], frozen: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    ref = (proto.get("ablation") or {}).get("reference") or {}
    items, skipped = [], []
    for f in proto["factors"]:
        r = ref.get(f, sampling.center(space[f]))
        cfg = {**frozen, f: r}
        if frozen.get(f) == r:
            skipped.append({"factor": f, "reason": "frozen value already equals the reference"})
        elif not _inside(space[f], r):
            skipped.append({"factor": f, "reason": "reference lies outside the narrowed space"})
        elif not sampling.feasible(cfg, [], proto.get("constraints", [])):
            skipped.append({"factor": f, "reason": "ablated configuration violates the protocol constraints"})
        else:
            items.append({"factor": f, "reference": r, "config": cfg})
    return {"items": items, "skipped": skipped}


def seeds(proto: dict[str, Any]) -> list[int]:
    return list(range(100, 100 + proto["confirm"]["seeds"]))  # the confirm seeds: paired with the frozen runs


def start(conn: sqlite3.Connection, kinds: KindRegistry, st: dict[str, Any], space: dict[str, Any],
          actor: str) -> bool:
    """Enqueue the ablation runs; False when the protocol has none or nothing is left to ablate."""
    if "ablation" not in st["protocol"]:
        return False
    p = plan(st["protocol"], space, st["data"]["frozen"])
    data = {**st["data"], "ablation": {"plan": p}}
    if not p["items"]:
        sd.save(conn, st["id"], data={**data, "ablation": {"plan": p, "results": []}})
        return False
    sd.save(conn, st["id"], stage=TAG, data=data)
    sd.enqueue(conn, kinds, sd.get(conn, st["id"]), TAG, "confirm", [i["config"] for i in p["items"]],
               seeds(st["protocol"]), actor)
    return True


def _t95(n: int) -> float:
    from scipy import stats

    return float(stats.t.ppf(0.975, n - 1))


def contribution(frozen: dict[int, float], ablated: dict[int, float], direction: str) -> dict[str, Any]:
    sign = 1.0 if direction == "max" else -1.0
    diffs = [sign * (frozen[s] - ablated[s]) for s in sorted(set(frozen) & set(ablated))]
    if len(diffs) < 2:
        return {"n": len(diffs), "mean": mean(diffs) if diffs else None, "sd": None, "ci95": None,
                "verdict": "insufficient paired runs"}
    m, s = mean(diffs), stdev(diffs)
    half = _t95(len(diffs)) * s / len(diffs) ** 0.5
    lo, hi = m - half, m + half
    verdict = ("contributes (interval above 0)" if lo > 0 else "removing it helps (interval below 0)" if hi < 0
               else "no detectable contribution at this n (interval includes 0)")
    return {"n": len(diffs), "mean": m, "sd": s, "ci95": [lo, hi], "verdict": verdict}


def finish(conn: sqlite3.Connection, st: dict[str, Any]) -> dict[str, Any] | None:
    """Summary once every ablation run is terminal, else None."""
    ch = sd.children(conn, st["id"], TAG)
    if not sd.all_done(ch):
        return None
    obj, proto = st["protocol"]["objective"], st["protocol"]
    best = st["data"]["confirm_configs"].index(st["data"]["frozen"])
    base = {r["seed"]: r["objective"] for r in sd.results(conn, sd.children(conn, st["id"], "confirm"), obj)
            if r["point"] == best and not r["failed"]}
    res = sd.results(conn, ch, obj)
    plan_ = st["data"]["ablation"]["plan"]
    out = []
    for i, item in enumerate(plan_["items"]):
        abl = {r["seed"]: r["objective"] for r in res if r["point"] == i and not r["failed"]}
        failed = sum(1 for r in res if r["point"] == i and r["failed"])
        out.append({"factor": item["factor"], "frozen": st["data"]["frozen"][item["factor"]],
                    "reference": item["reference"], "failed_runs": failed,
                    **contribution(base, abl, proto["direction"])})
    return {"plan": plan_, "results": out, "data": "validation (confirm seeds); not the test set"}
