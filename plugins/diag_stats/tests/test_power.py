"""Published formula checks, frozen statsmodels references and planning input boundaries."""

from __future__ import annotations

import math
from typing import Any

import pytest
from researchos.contracts.gateway import ToolBinding
from researchos.sdk.conformance import ToolConformance
from scipy.stats import norm

from helpers import GOLDEN, call
from researchos_plugin_diag_stats._common import StatsInputError
from researchos_plugin_diag_stats.power import BINDING, hanley_variance

BASE = {
    "design": "two_sample_t",
    "solve_for": "n",
    "effect": 0.5,
    "power": 0.8,
    "effect_source": "Cohen medium convention",
    "effect_basis": "cohen_convention",
}


def result(**kw: Any) -> dict[str, Any]:
    return call(BINDING, {"effect_source": "Synthetic planning scenario", **kw})["results"]


class TestPowerTool(ToolConformance):
    def make_binding(self) -> ToolBinding:
        return BINDING

    def sample_args(self) -> dict[str, Any]:
        return dict(BASE)


@pytest.mark.parametrize("case", GOLDEN["power"])
@pytest.mark.parametrize("solve", ["n", "power", "effect"])
def test_statsmodels_golden(case: dict[str, Any], solve: str) -> None:
    inputs = {"n": case["n"], "effect": case["effect"], "power": 0.8}
    inputs.pop(solve)
    got = result(**case["args"], solve_for=solve, **inputs)
    if solve == "n":
        assert got["n_continuous"] == pytest.approx(case["required_n"], rel=3e-6)
        assert got["power"] >= 0.8 - 1e-9
    else:
        assert got[solve] == pytest.approx(case["power" if solve == "power" else "mde"], rel=3e-6)


@pytest.mark.parametrize("effect,n", [(0.2, 394), (0.5, 64), (0.8, 26)])
def test_cohen_table_examples(effect: float, n: int) -> None:
    # Cohen 1988 table 2.4.1: .2 -> 393 (table approximation), .5 -> 64, .8 -> 26.
    # Exact noncentral t requires ceil(393.4057)=394, not the table's approximation.
    r = call(BINDING, {**BASE, "effect": effect})
    assert r["results"]["n"] == n
    assert r["n"] == 2 * n
    assert any("convention" in w for w in r["warnings"])


def test_buderer_original_worked_example() -> None:
    # Buderer 1996 p899 + Appendix A: N1=172.872, N2=61.23, total=173 (z rounded 1.96).
    r = result(design="diag_accuracy", solve_for="n", sensitivity=0.9, specificity=0.85, prevalence=0.2, half_width=0.1)
    assert r["n"] == 173
    assert r["sensitivity"]["n_total"] == 173
    assert r["specificity"]["n_total"] == 62
    assert r["sensitivity"]["n_continuous"] == pytest.approx(172.872, rel=4e-5)
    assert r["specificity"]["n_continuous"] == pytest.approx(61.2255, rel=4e-5)
    assert r["power"] is None


@pytest.mark.parametrize(
    "key,prevalence,expected", [("sensitivity", 0.2, 692), ("specificity", 0.2, 173), ("specificity", 0.8, 692)]
)
def test_buderer_single_endpoint(key: str, prevalence: float, expected: int) -> None:
    r = result(design="diag_accuracy", solve_for="n", prevalence=prevalence, half_width=0.05, **{key: 0.9})
    assert r["n"] == expected


@pytest.mark.parametrize(
    "auc,m,n,variance",
    [(0.5, 10, 20, 31 / 2400), (0.8, 50, 50, 557 / 281250), (0.8, 50, 100, 319 / 187500)],
)
def test_hanley_mcneil_formula(auc: float, m: int, n: int, variance: float) -> None:
    # Independent rational evaluation of Hanley-McNeil eq 1-2; unequal class sizes catch Q1/Q2 reversal.
    assert hanley_variance(auc, m, n) == pytest.approx(variance, rel=1e-12)


def test_auc_power_independent_normal_integration() -> None:
    r = result(design="auc", solve_for="power", effect=0.8, n=50, ratio=2)
    null_se = math.sqrt(151 / 60000)  # A=.5: (m+n+1)/(12*m*n)
    alt_se = math.sqrt(319 / 187500)
    critical = norm.ppf(0.975) * null_se
    expected = norm.cdf((0.8 - 0.5 - critical) / alt_se) + norm.cdf((0.5 - critical - 0.8) / alt_se)
    assert r["power"] == pytest.approx(expected, rel=1e-12, abs=1e-14)


@pytest.mark.parametrize(
    "design,extra,effect",
    [
        ("two_sample_t", {"ratio": 1.37}, 0.5),
        ("paired_t", {}, 0.5),
        ("one_way_anova", {"groups": 4}, 0.25),
        ("two_proportions", {"ratio": 0.7}, 0.3),
        ("auc", {"ratio": 1.7}, 0.7),
    ],
)
def test_roundtrip_allocation(design: str, extra: dict[str, Any], effect: float) -> None:
    r = result(design=design, solve_for="n", effect=effect, power=0.8, **extra)
    p = result(design=design, solve_for="power", effect=effect, n=r["n"], **extra)
    e = result(design=design, solve_for="effect", power=p["power"], n=r["n"], **extra)
    assert p["power"] == r["power"]
    assert p["power"] >= 0.8
    assert e["effect"] == pytest.approx(effect, abs=1e-8)
    if "ratio" in extra:
        assert 0 <= r["n_group2"] - r["n"] * extra["ratio"] < 1
        assert r["n_total"] == r["n"] + r["n_group2"]
    if design == "one_way_anova":
        assert r["n"] % 4 == 0
    if design == "paired_t":
        assert r["n_total"] == r["n"] and r["n_unit"] == "pairs"


@pytest.mark.parametrize(
    "patch",
    [
        {"design": "simulation"},
        {"design": "correlation"},
        {"design": []},
        {"solve_for": []},
        {"alpha": True},
        {"alpha": 0},
        {"alpha": 1},
        {"alpha": float("nan")},
        {"alpha": float("inf")},
        {"effect": -1},
        {"effect": float("nan")},
        {"effect": False},
        {"effect": 0},
        {"power": 0.01},
        {"power": 1},
        {"power": None},
        {"effect_source": " "},
        {"effect_source": None},
        {"effect_basis": "medium"},
        {"n": 10},
        {"ratio": 0},
        {"ratio": float("inf")},
        {"ratio": True},
        {"groups": 3},
        {"unexpected": 1},
        {"alternative": "invalid"},
        {"design": "one_way_anova", "groups": True},
        {"design": "paired_t", "ratio": 1},
        {"design": "two_proportions", "effect": 4},
        {"effect": 1e-20},
        {"design": "auc", "effect": 1},
        {"design": "auc", "effect": 0.4},
        {"design": "auc", "effect": 0.8, "alternative": "smaller"},
    ],
)
def test_invalid_input(patch: dict[str, Any]) -> None:
    with pytest.raises(StatsInputError):
        call(BINDING, {**BASE, **patch})


@pytest.mark.parametrize("n", [True, 1, 3.5, float("inf"), float("nan"), "20", None, 10_000_001])
def test_invalid_n(n: Any) -> None:
    with pytest.raises(StatsInputError):
        result(design="paired_t", solve_for="power", n=n, effect=0.5)


@pytest.mark.parametrize(
    "patch",
    [
        {"solve_for": "power"},
        {"sensitivity": 0},
        {"specificity": 1},
        {"prevalence": 0},
        {"prevalence": 1},
        {"half_width": 0},
        {"half_width": 1e-300},
        {"sensitivity": True},
        {"power": 0.8},
    ],
)
def test_diagnostic_validation(patch: dict[str, Any]) -> None:
    with pytest.raises(StatsInputError):
        result(
            **{
                "design": "diag_accuracy",
                "solve_for": "n",
                "sensitivity": 0.9,
                "prevalence": 0.2,
                "half_width": 0.1,
                **patch,
            }
        )


@pytest.mark.parametrize(
    "design,extra", [("paired_t", {}), ("two_sample_t", {}), ("two_proportions", {}), ("one_way_anova", {"groups": 4})]
)
def test_zero_effect_power_is_alpha(design: str, extra: dict[str, Any]) -> None:
    assert result(design=design, solve_for="power", n=40, effect=0, **extra)["power"] == 0.05


def test_unbalanced_anova_rejected() -> None:
    with pytest.raises(StatsInputError, match="balanced"):
        result(design="one_way_anova", solve_for="power", n=41, groups=4, effect=0.2)


def test_missing_diagnostic_endpoint() -> None:
    with pytest.raises(StatsInputError, match="sensitivity"):
        result(design="diag_accuracy", solve_for="n", prevalence=0.2, half_width=0.1)


@pytest.mark.parametrize("n,se", [(40, 0.0437), (60, 0.0356)])
def test_hanley_original_planning_example(n: int, se: float) -> None:
    # Hanley & McNeil 1982 p33: anticipated area .85, 40/60 in each class.
    assert math.sqrt(hanley_variance(0.85, n, n)) == pytest.approx(se, abs=0.00005)


@pytest.mark.parametrize("patch", [{"alpha": 5e-324}, {"effect": 10**400}])
def test_extreme_numbers_fail_cleanly(patch: dict[str, Any]) -> None:
    with pytest.raises(StatsInputError):
        call(BINDING, {**BASE, **patch})


def test_anova_rounding_respects_total_limit() -> None:
    from researchos_plugin_diag_stats.power import _allocation

    with pytest.raises(StatsInputError, match="rounded total"):
        _allocation({"design": "one_way_anova", "groups": 3}, 10_000_000)


def test_citing_cohen_does_not_imply_conventional_effect() -> None:
    r = call(BINDING, {**BASE, "effect_basis": "scientific", "effect_source": "Pilot estimate from Cohen et al."})
    assert not r["warnings"]


def test_hanley_near_perfect_auc_avoids_cancellation() -> None:
    from fractions import Fraction

    a = Fraction.from_float(0.999999999999)
    m, k = 100_000, 1000
    expected = (a * (1 - a) + (m - 1) * (a / (2 - a) - a * a) + (k - 1) * (2 * a * a / (1 + a) - a * a)) / (m * k)
    assert hanley_variance(float(a), m, k) == pytest.approx(float(expected), rel=1e-12, abs=0)


@pytest.mark.parametrize(
    "n,ratio,n2",
    [
        (100, 1.1, 110),
        (10, 0.7, 7),
        (1000, 0.3, 300),
        (7, 1.5, 11),
        (100, 2, 200),
        (300, 0.01, 3),
        (2, 100, 200),
        (30, 1 / 3, 10),
    ],
)
def test_group2_rounding_uses_decimal_ratio(n: int, ratio: float, n2: int) -> None:
    # Binary floats make 100 * 1.1 = 110.00000000000001; ceil must not add a spurious subject.
    r = result(design="two_sample_t", solve_for="power", effect=0.5, n=n, ratio=ratio)
    assert r["n_group2"] == n2
    assert r["n_total"] == n + n2


def test_auc_threshold_above_one_has_zero_power() -> None:
    # null .9, 20+20: null SE ~.0514 puts the one-sided-.025 rejection threshold above AUC 1 (unreachable).
    se0 = math.sqrt(hanley_variance(0.9, 20, 20))
    assert 0.9 + norm.isf(0.025) * se0 > 1
    assert 0.95 + norm.isf(0.05) * math.sqrt(hanley_variance(0.95, 20, 20)) > 1
    larger = result(design="auc", solve_for="power", null_auc=0.95, n=20, effect=0.99, alternative="larger")
    assert larger["power"] == 0
    # Two-sided keeps only the reachable lower tail (was .255 from the impossible upper tail).
    assert result(design="auc", solve_for="power", null_auc=0.9, n=20, effect=0.99)["power"] < 1e-20
    with pytest.raises(StatsInputError, match="infeasible"):
        result(design="auc", solve_for="effect", null_auc=0.9, n=20, power=0.2)


def test_auc_n_requires_reachable_threshold() -> None:
    r = result(design="auc", solve_for="n", null_auc=0.9, effect=0.99, power=0.2)
    n = r["n"]
    assert 0.9 + norm.isf(0.025) * math.sqrt(hanley_variance(0.9, n, n)) < 1
    assert r["power"] >= 0.2


def test_auc_rounded_n_meets_target_across_power_jump() -> None:
    # Codex cross-review: brentq converged onto the jump (n_continuous 19.99999999998 -> n=20, power 0).
    r = result(design="auc", solve_for="n", null_auc=0.9, effect=0.999, power=0.2, alpha=0.05156468981134984)
    assert r["power"] >= 0.2
    assert r["n"] == 21


def test_target_equal_to_integer_power_keeps_that_n() -> None:
    # Codex re-review: a target equal to the power at n=8 must not step to 9 on float noise.
    exact = result(design="paired_t", solve_for="power", effect=0.5, n=8)["power"]
    assert result(design="paired_t", solve_for="n", effect=0.5, power=exact)["n"] == 8


@pytest.mark.parametrize("alpha", [1e-11, 1e-15, 1e-20])
def test_alpha_below_numerical_floor_rejected(alpha: float) -> None:
    # Below ~1e-10 scipy's noncentral t/F tails lose accuracy (alpha 1e-20 gave power 0 or < alpha).
    with pytest.raises(StatsInputError, match="alpha"):
        result(design="one_way_anova", solve_for="power", groups=4, n=40, effect=0.5, alpha=alpha)


@pytest.mark.parametrize(
    "design,extra,expected",
    [
        # mpmath (50 digits) noncentral t / noncentral F references, n=20/group or 40 total, alpha 1e-10.
        ("two_sample_t", {"n": 20}, 7.662483114e-8),
        ("one_way_anova", {"n": 40, "groups": 4}, 3.176963029e-6),
    ],
)
def test_genome_wide_scale_alpha_accurate(design: str, extra: dict[str, Any], expected: float) -> None:
    r = result(design=design, solve_for="power", effect=0.5, alpha=1e-10, **extra)
    assert r["power"] == pytest.approx(expected, rel=1e-6)
