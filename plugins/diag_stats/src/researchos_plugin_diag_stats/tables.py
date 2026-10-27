"""Contingency-table branch of stats_compare_groups: Fisher's exact test (2x2) or Pearson chi-square. Choosing Fisher
when an expected count is < 5 concerns the validity of the chi-square approximation, not a pretest of the data."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from scipy import stats

from . import effects
from ._common import Report, StatsInputError, apa, count_matrix, p_text

TESTS = ("chi_square", "fisher")
REFS = {
    "chi_square": "Pearson K (1900) Philos Mag 50:157-175 (no continuity correction); Cochran WG (1954) Biometrics "
    "10:417-451 (expected counts)",
    "fisher": "Fisher RA (1922) J R Stat Soc 85:87-94; conditional odds ratio CI: Cornfield J (1956)",
}
RULE = "table: Fisher exact if 2x2 and any expected count < 5, else Pearson chi-square (no continuity correction)."


def _finite(x: Any) -> float | None:
    v = float(x)
    return v if math.isfinite(v) else None


def _fisher(t: Any, conf: float, check: dict[str, Any], rep: Report) -> dict[str, Any]:
    if t.shape != (2, 2):
        raise StatsInputError("fisher needs a 2x2 table")
    res = stats.contingency.odds_ratio(t, kind="conditional")
    ci = res.confidence_interval(conf)
    p = float(stats.fisher_exact(t)[1])
    value, lo, hi = (_finite(x) for x in (res.statistic, ci.low, ci.high))
    note = None
    if None in (value, lo, hi):  # review #39: a zero cell makes the MLE / a bound 0 or infinite
        note = "a zero cell: the conditional MLE or a CI bound is infinite and reported as null"
        rep.warn(note)
    eff = effects.effect("odds_ratio", value, [lo, hi], note)  # type: ignore[list-item]
    text = f"Fisher's exact test, {p_text(p)}"
    if value is not None and lo is not None and hi is not None:
        text += f", OR = {value:.2f}, {round(conf * 100)}% CI [{lo:.2f}, {hi:.2f}]"
    return {"p": p, "effect": eff, "expected_counts": check, "apa": text}


def table_test(t: Any, test: str, conf: float, rep: Report) -> dict[str, Any]:
    chi2, p, dof, expected = stats.chi2_contingency(t, correction=False)
    low = int((expected < 5).sum())
    check = {"expected_min": float(expected.min()), "cells_expected_below_5": low, "cells": int(expected.size)}
    if test == "fisher":
        return _fisher(t, conf, check, rep)
    if low and (low > 0.2 * expected.size or expected.min() < 1):
        rep.warn("chi-square approximation doubtful: >20% of expected counts < 5 or one < 1 (Cochran)")
    eff = effects.cramers_v(float(chi2), int(dof), int(t.sum()), min(t.shape), conf)
    return {
        "statistic": float(chi2),
        "df": int(dof),
        "p": float(p),
        "effect": eff,
        "expected_counts": check,
        "apa": apa("χ²", f"{dof}, N = {int(t.sum())}", chi2, p, eff, conf),
    }


def run_table(name: str, args: Mapping[str, Any], test: str, conf: float) -> str:
    t = count_matrix(args.get("table"), "table", square=False, max_dim=20)
    if (t.sum(0) == 0).any() or (t.sum(1) == 0).any():
        raise StatsInputError("table has an empty row or column")
    if test == "auto":
        test = "fisher" if t.shape == (2, 2) and (stats.contingency.expected_freq(t) < 5).any() else "chi_square"
    rep = Report(
        name, {"fisher": "Fisher's exact test", "chi_square": "Pearson chi-square test"}[test], REFS[test], int(t.sum())
    )
    rep.assumptions += ["Observations are independent and each falls in exactly one cell."]
    return rep.emit(table_test(t, test, conf, rep), test=test, rule=RULE, confidence=conf)
