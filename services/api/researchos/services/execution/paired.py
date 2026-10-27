"""Paired comparison of two runs on the same evaluation windows (order f, ADR-191): the "budgeted comparison of a
few justified improvements" with effect sizes and power. Statistics come from the diag_stats tools through the tool
registry (stats_paired_tests: McNemar exact / mid-p; stats_power: minimum detectable effect) - nothing is
reimplemented here; if the plugin is absent the comparison says so.

Both runs must have succeeded on the same plan dataset and split (same split hash), and their saved predictions for
the compared split must cover the same sample ids with the same true labels, and they must use the same seed (a
paired comparison of configurations, not of seeds; review #71). At least two windows are needed (review #69).
Reported: accuracies, difference (B - A) with Newcombe's hybrid score interval for paired proportions (method 10;
stays inside [-1, 1] and does not collapse at 0 / n, review #70), Cohen's h, McNemar p, and the minimum detectable
Cohen's h at 80% power for this number of windows. Caveat stated in the output: windows from one subject are not
independent, so with few subjects these intervals and p-values are optimistic."""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...contracts.gateway import ToolError
from ...kernel.db import row
from ..gateway.tools import ToolRegistry, execute

Z95 = 1.959963984540054
NEWCOMBE = "Newcombe RG (1998) Stat Med 17:2635-2650, method 10 (Wilson score limits, phi-corrected)"


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    p, z2 = k / n, z * z
    centre, half = (p + z2 / (2 * n)) / (1 + z2 / n), z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe_paired(both: int, only_b: int, only_a: int, neither: int, z: float = Z95) -> tuple[float, float]:
    """95% interval for p_B - p_A from paired outcomes (B correct = rows, A correct = columns)."""
    n = both + only_b + only_a + neither
    p1, p2 = (both + only_b) / n, (both + only_a) / n
    l1, u1 = wilson(both + only_b, n, z)
    l2, u2 = wilson(both + only_a, n, z)
    den = (both + only_b) * (only_a + neither) * (both + only_a) * (only_b + neither)
    phi = (both * neither - only_b * only_a) / math.sqrt(den) if den else 0.0
    d = math.sqrt(max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2))
    e = math.sqrt(max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2))
    diff = p1 - p2
    return max(-1.0, diff - d), min(1.0, diff + e)


def _run(conn: sqlite3.Connection, runs_dir: Path, run_id: str) -> dict[str, Any]:
    r = row(conn.execute("SELECT state FROM runs WHERE id=?", (run_id,)))
    if r is None:
        raise not_found("run")
    if r["state"] != "succeeded":
        raise ApiError(409, "run_not_succeeded", f"run {run_id} is {r['state']}; compare succeeded runs only")
    manifest: dict[str, Any] = json.loads((runs_dir / run_id / "run.json").read_text(encoding="utf-8"))
    return manifest


def _predictions(runs_dir: Path, run_id: str, split: str) -> dict[str, tuple[str, str]]:
    p = runs_dir / run_id / "predictions" / f"{split}.csv"
    if not p.is_file():
        raise ApiError(409, "no_predictions", f"run {run_id} saved no predictions for {split}")
    with p.open(newline="", encoding="utf-8") as fh:
        return {r["sample_id"]: (r["y_true"], r["y_pred"]) for r in csv.DictReader(fh)}


def _tool(tools: ToolRegistry, name: str, args: dict[str, Any]) -> dict[str, Any]:
    t = tools.get(name)
    if t is None:
        raise ApiError(503, "missing_tool", f"{name} (diag_stats plugin) is not installed")
    try:
        out: dict[str, Any] = json.loads(execute(t, args, frozenset()))
    except ToolError as e:
        raise ApiError(422, "stats_input", str(e)) from None
    return out


def compare(conn: sqlite3.Connection, runs_dir: Path, tools: ToolRegistry, run_a: str, run_b: str,
            split: str) -> dict[str, Any]:
    ma, mb = _run(conn, runs_dir, run_a), _run(conn, runs_dir, run_b)
    if (ma["dataset"], ma["split_hash"]) != (mb["dataset"], mb["split_hash"]):
        raise ApiError(409, "not_comparable", "runs used different datasets or splits")
    if ma.get("seed") != mb.get("seed"):
        raise ApiError(409, "not_comparable", "runs used different seeds; compare configurations at the same seed")
    pa, pb = _predictions(runs_dir, run_a, split), _predictions(runs_dir, run_b, split)
    if set(pa) != set(pb) or any(pa[k][0] != pb[k][0] for k in pa):
        raise ApiError(409, "not_comparable", "the runs' predictions cover different windows or labels")
    ca = {k: v[0] == v[1] for k, v in pa.items()}
    cb = {k: v[0] == v[1] for k, v in pb.items()}
    n = len(ca)
    if n < 2:
        raise ApiError(422, "too_few_windows", f"{n} window(s) in {split}: a paired comparison needs at least 2")
    both = sum(ca[k] and cb[k] for k in ca)
    only_a = sum(ca[k] and not cb[k] for k in ca)
    only_b = sum(cb[k] and not ca[k] for k in ca)
    table = [[both, only_a], [only_b, n - both - only_a - only_b]]
    acc_a, acc_b = (both + only_a) / n, (both + only_b) / n
    diff = (only_b - only_a) / n
    h = 2 * math.asin(math.sqrt(acc_b)) - 2 * math.asin(math.sqrt(acc_a))
    mcnemar = _tool(tools, "stats_paired_tests", {"test": "mcnemar", "table": table})
    mde = _tool(tools, "stats_power", {"design": "two_proportions", "solve_for": "effect", "n": n, "power": 0.8,
                                       "effect_source": "minimum detectable difference for this number of windows"})
    return {
        "run_a": run_a, "run_b": run_b, "split": split, "windows": n, "table": table,
        "table_layout": "rows: A correct / A wrong; columns: B correct / B wrong",
        "accuracy_a": acc_a, "accuracy_b": acc_b,
        "difference_b_minus_a": diff,
        "difference_ci95": list(newcombe_paired(both, only_b, only_a, n - both - only_a - only_b)),
        "difference_ci_method": NEWCOMBE,
        "cohen_h": h,
        "mcnemar": mcnemar["results"], "mcnemar_reference": mcnemar["formula_reference"],
        "minimum_detectable_h_80": mde["results"]["effect"],
        "power_note": "two-proportion approximation (independent groups); a paired design is usually more powerful",
        "caveat": "windows from the same subject are not independent: with few subjects these intervals and p-values "
                  "are optimistic; subject-level replication is needed for a claim",
    }
