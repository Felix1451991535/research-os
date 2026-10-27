"""Study search proposals with failure memory (ADR-168/173): the batch the protocol's sampler runs next. Failure
memory is rebuilt from ALL finished runs before every batch (screen and search; review #59): failure regions inferred
from every result become avoid rules, and exact failed configurations are never proposed again. Failed runs are
kept apart from objective observations (TPE is told them as failed trials). Shared by `study` and `advisor`.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..verification.regions import failure_rules
from . import bayes, plans, sampling
from . import study_data as sd


def memory(st: dict[str, Any], results: list[dict[str, Any]], space: dict[str, Any]) -> tuple[list[str], set[str]]:
    """(avoid rules, failed configuration keys) from every finished run."""
    factors = st["protocol"]["factors"]
    try:
        rules = failure_rules([{k: float(r[k]) for k in factors} for r in results], [r["failed"] for r in results])
    except (TypeError, ValueError):  # categorical factors: exact-configuration memory only
        rules = []
    avoid = sorted(set(st["data"].get("screen", {}).get("avoid", [])) | {r["rule"] for r in rules})
    failed = {sd.config_key(r, list(space)) for r in results if r["failed"]}
    return avoid, failed


def propose(conn: sqlite3.Connection, st: dict[str, Any], batch_no: int,
            results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deterministic per batch number; points satisfy the constraints, avoid failure regions and never repeat a
    failed configuration. Empty list = nothing feasible (info says why)."""
    proto = st["protocol"]
    rev = plans.get_revision(conn, st["plan_id"], st["current_revision"])
    space, fixed, s = rev["content"]["space"], rev["content"].get("params", {}), proto["search"]
    avoid, failed = memory(st, results, space)
    constraints = proto.get("constraints", [])
    hist = [(r, r["objective"]) for r in results if not r["failed"]]
    if s["sampler"] == "tpe":
        bad = [r for r in results if r["failed"]]
        pts = sampling.tpe_batch(space, fixed, hist, s["batch"] * 2, 1000 + batch_no, proto["direction"], avoid,
                                 constraints, failed=bad)
        info: dict[str, Any] = {"model": "optuna TPE"}
    elif s["sampler"] == "gp_ei":
        pts, info = bayes.gp_ei_batch(space, fixed, hist, s["batch"] * 2, 3000 + batch_no, proto["direction"], avoid,
                                      constraints)
    else:
        pts = sampling.qmc_batch(space, fixed, s["batch"] * 2, seed=2026, skip=batch_no * s["batch"] * 8,
                                 avoid=avoid, constraints=constraints)
        info = {"model": None, "reason": "scrambled Sobol (model-free)"}
    keep = [i for i, p in enumerate(pts) if sd.config_key(p, list(space)) not in failed][: s["batch"]]
    if "points" in info:
        info["points"] = [info["points"][i] for i in keep]
        info["max_ei"] = info["points"][0]["ei"] if info["points"] else None
    if not keep:
        reason = ("failure memory: runs failed wherever they were tried (unconditional failure region); searching "
                  "further would repeat failures" if sampling.ALWAYS in avoid else
                  "no feasible candidate left (constraints, failure regions and failed configurations exclude the "
                  "remaining space)")
        info = {**info, "model": info.get("model"), "reason": reason}
    return [pts[i] for i in keep], {**info, "avoid": avoid, "failed_configurations": len(failed)}
