"""Next-experiment advice for a study (ADR-168): read-only. Sensitivity from the screen ANOVA, uncertainty
decomposition (seed noise vs differences between configurations), the batch the protocol's sampler would run next,
failure memory, and explicit stop rules evaluated on the data. Nothing here launches runs or touches the test set;
`study.advance` (under the plan's approval) does the work.
"""

from __future__ import annotations

import math
import sqlite3
from statistics import mean, pstdev, stdev
from typing import Any

from . import search, study
from . import study_data as sd

SEARCH = "search"


def sensitivity(screen: dict[str, Any]) -> dict[str, Any]:
    a = screen.get("anova") or {}
    effects = sorted(a.get("effects", []), key=lambda e: -(e.get("partial_eta2") or 0.0))
    return {
        "ranking": [{k: e.get(k) for k in ("term", "partial_eta2", "p_holm", "reported", "statement")}
                    for e in effects],
        "basis": "screen ANOVA (2^k + centre points): partial eta^2 within this setup; unreported or non-estimable "
                 "terms are not evidence of no effect",
        "notes": a.get("notes", []),
    }


def uncertainty(results: list[dict[str, Any]], factors: list[str]) -> dict[str, Any]:
    """Pooled within-configuration (seed) SD vs SD of configuration means."""
    groups: dict[tuple[Any, ...], list[float]] = {}
    for r in results:
        if not r["failed"]:
            groups.setdefault(tuple(r[f] for f in factors), []).append(r["objective"])
    reps = [v for v in groups.values() if len(v) > 1]
    dof = sum(len(v) - 1 for v in reps)
    seed_sd = math.sqrt(sum(stdev(v) ** 2 * (len(v) - 1) for v in reps) / dof) if dof else None
    config_sd = pstdev([mean(v) for v in groups.values()]) if len(groups) > 1 else None
    if seed_sd is None or config_sd is None:
        advice = "not enough replicated configurations to separate seed noise from configuration differences"
    elif seed_sd >= config_sd:
        advice = "seed noise is as large as the differences between configurations: add seeds before exploring"
    else:
        advice = "differences between configurations exceed seed noise: exploring configurations is informative"
    return {"seed_sd": seed_sd, "config_sd": config_sd, "configurations": len(groups),
            "replicated_configurations": len(reps), "advice": advice}


def stop_rules(st: dict[str, Any], info: dict[str, Any], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proto, data = st["protocol"], st["data"]
    sigma = (data.get("screen", {}).get("anova") or {}).get("sigma") or 0.0
    batches = data.get("search_batches", 0)
    used = batches * proto["search"]["batch"]
    max_ei = info.get("max_ei")
    failed = sum(r["failed"] for r in results)
    return [
        {"rule": "budget", "met": used >= proto["search"]["max_trials"],
         "detail": f"{used} of {proto['search']['max_trials']} search configurations used"},
        {"rule": "patience", "met": study.stalled(data.get("best_history", []), sigma,
                                                  proto["search"]["patience_batches"]),
         "detail": f"best value moved by <= pure-error SD ({sigma:.4g}) over the last "
                   f"{proto['search']['patience_batches']} batches"},
        {"rule": "expected_improvement", "met": max_ei is not None and max_ei < sigma,
         "detail": "largest model-expected improvement below the pure-error SD" if max_ei is not None
         else "no model (sampler is not gp_ei or too little history)"},
        {"rule": "failure_rate", "met": bool(results) and failed / len(results) > 0.5,
         "detail": f"{failed} of {len(results)} runs failed"},
    ]


def advise(conn: sqlite3.Connection, sid: str) -> dict[str, Any]:
    st = sd.get(conn, sid)
    results = study.all_results(conn, st)
    screen = st["data"].get("screen", {})
    in_search = st["stage"] in (SEARCH, "screen")
    batch = st["data"].get("search_batches", 0)
    proposals, info = search.propose(conn, st, batch, results) if in_search else ([], {})
    rules = stop_rules(st, info, results)
    met = [r["rule"] for r in rules if r["met"]]
    if not in_search:
        recommendation = f"search is over (stage {st['stage']}); follow the protocol's remaining stages"
    elif met:
        recommendation = "stop searching: " + ", ".join(met)
    else:
        recommendation = "continue: advance the study to run the proposed batch"
    return {
        "study_id": sid, "stage": st["stage"], "objective": st["protocol"]["objective"],
        "direction": st["protocol"]["direction"],
        "sensitivity": sensitivity(screen),
        "uncertainty": uncertainty(results, st["protocol"]["factors"]),
        "failure_memory": {"rules": screen.get("failure_rules", []),
                           "avoided_regions": info.get("avoid", screen.get("avoid", [])),
                           "failed_configurations": info.get("failed_configurations", 0),
                           "constraints": st["protocol"].get("constraints", [])},
        "proposals": [{"params": {k: v for k, v in p.items() if not k.startswith("_")},
                       **((info.get("points") or [{}] * len(proposals))[i])} for i, p in enumerate(proposals)],
        "model": {k: v for k, v in info.items() if k in ("model", "reason", "max_ei")},
        "stop_rules": rules,
        "ablation": st["data"].get("ablation"),
        "recommendation": recommendation,
        "caveat": "proposals say where to look next; model numbers are estimates, not results",
    }
