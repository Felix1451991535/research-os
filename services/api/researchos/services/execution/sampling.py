"""Experiment designs and bounded samplers. All points lie inside the approved space; sampling is seeded."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any


def _levels(spec: dict[str, Any]) -> tuple[Any, Any, Any]:
    """(low, high, center) for a parameter spec {low, high, log?} or {choices}."""
    if "choices" in spec:
        c = spec["choices"]
        return c[0], c[-1], c[len(c) // 2]
    lo, hi = spec["low"], spec["high"]
    center = math.sqrt(lo * hi) if spec.get("log") else (lo + hi) / 2
    if spec.get("int"):
        center = round(center)
    return lo, hi, center


def center(spec: dict[str, Any]) -> Any:
    """Center level of a parameter spec (geometric for log ranges, rounded for ints, middle choice)."""
    return _levels(spec)[2]


def factorial_with_center(
    space: dict[str, Any], factors: list[str], fixed: dict[str, Any], center_points: int
) -> list[dict[str, Any]]:
    """2^k corners over `factors` (others at center) + `center_points` center points (curvature test)."""
    centers = {k: center(v) for k, v in space.items()}
    pts = []
    for combo in itertools.product(*[(_levels(space[f])[0], _levels(space[f])[1]) for f in factors]):
        pts.append({**fixed, **centers, **dict(zip(factors, combo, strict=True)), "_center": False})
    pts += [{**fixed, **centers, "_center": True} for _ in range(center_points)]
    return pts


def _scale(u: float, spec: dict[str, Any]) -> Any:
    if "choices" in spec:
        c = spec["choices"]
        return c[min(len(c) - 1, int(u * len(c)))]
    lo, hi = spec["low"], spec["high"]
    if lo == hi:  # a fixed parameter keeps its exact value (exp(log(0.1)) != 0.1)
        return lo
    v = math.exp(math.log(lo) + u * (math.log(hi) - math.log(lo))) if spec.get("log") else lo + u * (hi - lo)
    return round(v) if spec.get("int") else v


SAMPLERS = ("qmc", "tpe", "gp_ei")
ALWAYS = "always"  # verification.regions: an unconditional failure region
_OPS = {"<=": lambda a, b: a <= b, "<": lambda a, b: a < b, ">=": lambda a, b: a >= b, ">": lambda a, b: a > b}


def satisfies(point: dict[str, Any], condition: str) -> bool:
    """condition = 'batch <= 64' (protocol constraint: name, one of <= < >= >, number)."""
    name, op, thr = condition.split(" ")
    return bool(_OPS[op](float(point[name]), float(thr)))


def check_rules(space: dict[str, Any], constraints: list[str]) -> None:
    """Constraints must name a numeric space parameter, use a known operator and a numeric threshold."""
    for c in constraints:
        parts = c.split(" ")
        ok = len(parts) == 3 and parts[0] in space and "choices" not in space[parts[0]] and parts[1] in _OPS
        try:
            float(parts[2]) if ok else None
        except ValueError:
            ok = False
        if not ok:
            raise ValueError(f"bad constraint {c!r}: use '<numeric parameter> <=|<|>=|> <number>'")


def tighten(space: dict[str, Any], constraints: Sequence[str]) -> dict[str, Any]:
    """The space narrowed by box constraints (each is one parameter bound), so designs and samplers stay inside;
    integer bounds round inwards. ValueError when a range becomes empty."""
    out = {k: dict(v) for k, v in space.items()}
    for c in constraints:
        name, op, raw = c.split(" ")
        spec, thr = out[name], float(raw)
        if spec.get("int"):
            thr = (math.floor(thr) - (op == "<" and thr == math.floor(thr))) if op in ("<", "<=") else (
                math.ceil(thr) + (op == ">" and thr == math.ceil(thr)))
        elif op in ("<", ">"):  # open bound on a float: the nearest representable value inside (review #57)
            thr = math.nextafter(thr, -math.inf if op == "<" else math.inf)
        if op in ("<", "<="):
            spec["high"] = min(spec["high"], thr)
        else:
            spec["low"] = max(spec["low"], thr)
        if spec["low"] > spec["high"]:
            raise ValueError(f"constraint {c!r} leaves no values of {name}")
    return out


def feasible(point: dict[str, Any], avoid: Sequence[str], constraints: Sequence[str]) -> bool:
    """Failure memory (avoided failure regions) and protocol constraints, applied to every sampler (ADR-168)."""
    return not any(in_region(point, r) for r in avoid) and all(satisfies(point, c) for c in constraints)


def in_region(point: dict[str, Any], rule: str) -> bool:
    """rule = 'a > 0.3 and b <= 24' (from failure_rules); "always" (a root leaf: failures wherever runs were
    tried) covers the whole space (review #65)."""
    if rule == ALWAYS:
        return True
    for cond in rule.split(" and "):
        name, op, thr = cond.split(" ")
        v = float(point[name])
        if (op == ">" and not v > float(thr)) or (op == "<=" and not v <= float(thr)):
            return False
    return True


def qmc_batch(
    space: dict[str, Any], fixed: dict[str, Any], n: int, seed: int, skip: int, avoid: Sequence[str],
    constraints: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Scrambled Sobol points (seeded), skipping already-used ones; infeasible points (failure regions,
    constraints) dropped."""
    from scipy.stats import qmc

    names = sorted(space)
    sampler = qmc.Sobol(d=len(names), scramble=True, seed=seed)
    if skip:
        sampler.fast_forward(skip)
    out = []
    for row in sampler.random(n * 4):
        p = {**fixed, **{k: _scale(float(u), space[k]) for k, u in zip(names, row, strict=True)}}
        if feasible(p, avoid, constraints):
            out.append(p)
        if len(out) == n:
            break
    return out


def tpe_batch(
    space: dict[str, Any],
    fixed: dict[str, Any],
    history: list[tuple[dict[str, Any], float]],
    n: int,
    seed: int,
    direction: str,
    avoid: Sequence[str] = (),
    constraints: Sequence[str] = (),
    failed: Sequence[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Optuna TPE via ask/tell, rebuilt deterministically from the finished history each batch; failed runs are
    added as failed trials (never as objective values); infeasible asks (failure regions, constraints) are told as
    failed and re-asked, at most 50 per point."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        direction="maximize" if direction == "max" else "minimize", sampler=optuna.samplers.TPESampler(seed=seed)
    )
    dists: dict[str, optuna.distributions.BaseDistribution] = {
        k: (
            optuna.distributions.CategoricalDistribution(v["choices"])
            if "choices" in v
            else optuna.distributions.IntDistribution(v["low"], v["high"], log=bool(v.get("log")))
            if v.get("int")
            else optuna.distributions.FloatDistribution(v["low"], v["high"], log=bool(v.get("log")))
        )
        for k, v in space.items()
    }
    for params, value in history:
        study.add_trial(
            optuna.trial.create_trial(params={k: params[k] for k in space}, distributions=dists, value=value)
        )
    for params in failed:
        study.add_trial(optuna.trial.create_trial(params={k: params[k] for k in space}, distributions=dists,
                                                  state=optuna.trial.TrialState.FAIL))
    out: list[dict[str, Any]] = []
    for _ in range(50 * n):
        trial = study.ask(dists)
        point = {**fixed, **trial.params}
        if feasible(point, avoid, constraints):
            out.append(point)
            if len(out) == n:
                break
        else:
            study.tell(trial, state=optuna.trial.TrialState.FAIL)
    return out
