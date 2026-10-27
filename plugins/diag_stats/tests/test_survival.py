"""stats_survival. Golden: the Rossi recidivism data (Rossi, Berk & Lenihan 1980, bundled with lifelines) with R
`coxph(Surv(week, arrest) ~ fin + age + race + wexp + mar + paro + prio)` as printed in Fox & Weisberg (2011)
"Cox Proportional-Hazards Regression for Survival Data in R", appendix to An R Companion to Applied Regression.
Kaplan-Meier, log-rank and Harrell's C are checked against independent implementations written here."""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pytest
from researchos.contracts.gateway import ToolBinding
from researchos.sdk.conformance import ToolConformance
from scipy import stats

from helpers import call
from researchos_plugin_diag_stats import survival
from researchos_plugin_diag_stats._common import StatsInputError

lifelines = pytest.importorskip("lifelines")
from lifelines.datasets import load_rossi  # noqa: E402

ROSSI = load_rossi()
BASE = {"durations": ROSSI.week.astype(float).tolist(), "events": ROSSI.arrest.astype(int).tolist()}
COVARIATES = ("fin", "age", "race", "wexp", "mar", "paro", "prio")
R_COEF = {
    "fin": -0.37942,
    "age": -0.05744,
    "race": 0.31390,
    "wexp": -0.14980,
    "mar": -0.43370,
    "paro": -0.08487,
    "prio": 0.09150,
}


def res(args: dict[str, Any]) -> dict[str, Any]:
    return call(survival.BINDING, args)


class TestSurvivalTool(ToolConformance):
    def make_binding(self) -> ToolBinding:
        return survival.BINDING

    def sample_args(self) -> dict[str, Any]:
        return {**BASE, "analysis": "km", "groups": ROSSI.prio.clip(upper=9).tolist(), "times": list(range(0, 50))}


def test_cox_matches_r_on_rossi() -> None:
    out = res({**BASE, "analysis": "cox", "covariates": {c: ROSSI[c].astype(float).tolist() for c in COVARIATES}})
    r = out["results"]
    for c in r["coefficients"]:
        assert c["coef"] == pytest.approx(R_COEF[c["name"]], abs=5e-5), c["name"]
        assert c["hazard_ratio"] == pytest.approx(math.exp(c["coef"]))
    assert r["likelihood_ratio"]["statistic"] == pytest.approx(33.27, abs=0.005) and r["likelihood_ratio"]["df"] == 7
    assert r["likelihood_ratio"]["p"] == pytest.approx(2.362e-05, rel=1e-3) and r["concordance"] == pytest.approx(
        0.640, abs=0.001
    )
    # Fox & Weisberg: the Schoenfeld-residual check finds non-proportional hazards for age and wexp
    assert {t["name"] for t in r["ph_check"]["tests"] if t["p"] < 0.05} == {"age", "wexp"}
    assert any("['age', 'wexp']" in w for w in out["warnings"])


def _logrank(t: Any, e: Any, g: Any) -> float:
    """Two-group log-rank chi-square: sum (O1 - E1)^2 / sum V1 over distinct event times (hypergeometric V)."""
    o_e = v = 0.0
    for time in np.unique(t[e == 1]):
        at_risk = t >= time
        n, n1 = at_risk.sum(), (at_risk & (g == 1)).sum()
        d = ((t == time) & (e == 1)).sum()
        d1 = ((t == time) & (e == 1) & (g == 1)).sum()
        o_e += d1 - d * n1 / n
        v += d * (n1 / n) * (1 - n1 / n) * (n - d) / (n - 1) if n > 1 else 0.0
    return o_e**2 / v


def test_logrank_matches_an_independent_implementation() -> None:
    r = res({**BASE, "analysis": "logrank", "groups": ROSSI.fin.tolist()})["results"]
    expected = _logrank(ROSSI.week.to_numpy(), ROSSI.arrest.to_numpy(), ROSSI.fin.to_numpy())
    assert r["statistic"] == pytest.approx(expected, rel=1e-9) and r["df"] == 1
    assert r["p"] == pytest.approx(stats.chi2.sf(expected, 1), rel=1e-9)


def test_km_greenwood_loglog_by_hand() -> None:
    t = np.array([3, 5, 5, 8, 10, 12, 12, 15, 18, 20], float)
    e = np.array([1, 1, 0, 1, 0, 1, 1, 0, 1, 0])
    g = res({"durations": t.tolist(), "events": e.tolist(), "analysis": "km", "times": [9, 16]})["results"]["groups"][0]
    s, var_sum, z, by_time = 1.0, 0.0, stats.norm.ppf(0.975), {}
    for time in np.unique(t[e == 1]):
        n, d = (t >= time).sum(), ((t == time) & (e == 1)).sum()
        s *= 1 - d / n
        var_sum += d / (n * (n - d))
        se = math.sqrt(var_sum) / abs(math.log(s))  # Greenwood on the log(-log) scale
        c = math.log(-math.log(s))
        by_time[time] = (s, [math.exp(-math.exp(c + z * se)), math.exp(-math.exp(c - z * se))])
    for row, time in zip(g["at_times"], (8.0, 15.0), strict=True):  # last event time at or before 9 and 16
        expected = by_time[time if time in by_time else max(k for k in by_time if k <= time)]
        assert row["survival"] == pytest.approx(expected[0]) and row["ci"] == pytest.approx(expected[1], rel=1e-6)
    assert g["median"] == 12.0 and g["events"] == 6  # S(12) = 0.411 is the first value <= 0.5


def test_concordance_by_pair_enumeration() -> None:
    rng = np.random.default_rng(4)
    t = rng.integers(1, 20, 40).astype(float)
    e = rng.integers(0, 2, 40)
    e[0] = 1
    risk = np.round(rng.normal(0, 1, 40) - 0.05 * t, 1)  # ties in risk and in time
    num = den = 0.0
    for i, j in itertools.permutations(range(40), 2):
        # comparable: i has the event first; an event and a censoring at the same time count (censored outlived)
        if e[i] and (t[i] < t[j] or (t[i] == t[j] and not e[j])):
            den += 1
            num += 1.0 if risk[i] > risk[j] else 0.5 if risk[i] == risk[j] else 0.0
    out = res({"durations": t.tolist(), "events": e.tolist(), "analysis": "concordance", "risk_scores": risk.tolist()})
    assert out["results"]["c_index"] == pytest.approx(num / den) and any("no CI" in w for w in out["warnings"])


@pytest.mark.parametrize(
    "args,message",
    [
        ({"durations": [1, 2, 3], "events": [0, 0, 0], "analysis": "km"}, "no events"),
        ({"durations": [1, 2, 3], "events": [1, 0], "analysis": "km"}, "same length"),
        ({"durations": [1, 2, 3], "events": [1, 0, 1], "analysis": "logrank"}, "needs groups"),
        (
            {"durations": [1, 2, 3, 4], "events": [1, 0, 1, 1], "analysis": "cox", "covariates": {"x": [1, 1, 1, 1]}},
            "constant covariates",
        ),
        ({"durations": [1, 2, 3], "events": [1, 0, 1], "analysis": "nope"}, "analysis must be"),
    ],
)
def test_invalid_input(args: dict[str, Any], message: str) -> None:
    with pytest.raises(StatsInputError, match=message):
        survival.run(args)


def test_missing_extra_is_an_input_error(monkeypatch: Any) -> None:
    import builtins

    real = builtins.__import__

    def deny(name: str, *a: Any, **k: Any) -> Any:
        if name == "lifelines":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    with pytest.raises(StatsInputError, match=r"diag_stats\[survival\]"):
        survival.lifelines()
