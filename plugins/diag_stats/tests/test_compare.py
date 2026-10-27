"""stats_compare_groups. Goldens: R datasets::sleep (Cushny & Peebles 1905, via Student 1908) with R t.test /
wilcox.test / shapiro.test output; Darwin's corn differences (SciPy wilcoxon docs: W = 24, p = 0.041259765625);
Fisher's tea table with R fisher.test; Cohen's d_z example (Pingouin docs). Exact permutation p-values are checked
against an independent enumeration here. Interval coverage is checked by seeded simulation across magnitudes."""

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
from researchos_plugin_diag_stats import compare, effects
from researchos_plugin_diag_stats._common import StatsInputError

DRUG1 = [0.7, -1.6, -0.2, -1.2, -0.1, 3.4, 3.7, 0.8, 0.0, 2.0]
DRUG2 = [1.9, 0.8, 1.1, 0.1, -0.1, 4.4, 5.5, 1.6, 4.6, 3.4]
SLEEP = [{"name": "drug1", "values": DRUG1}, {"name": "drug2", "values": DRUG2}]
CORN = [6, 8, 14, 16, 23, 24, 28, 29, 41, -48, 49, 56, 60, -67, 75]


def groups(*values: Any, **kw: Any) -> dict[str, Any]:
    return {"groups": [{"name": f"g{i}", "values": [float(x) for x in v]} for i, v in enumerate(values)], **kw}


def res(args: dict[str, Any]) -> dict[str, Any]:
    return call(compare.BINDING, args)


class TestCompareGroupsTool(ToolConformance):
    def make_binding(self) -> ToolBinding:
        return compare.BINDING

    def sample_args(self) -> dict[str, Any]:  # largest allowed input: output must stay bounded
        rng = np.random.default_rng(0)
        return {
            "groups": [{"name": f"g{i}", "values": rng.normal(i, 1, compare.MAX_VALUES).tolist()} for i in range(10)]
        }


@pytest.mark.parametrize(
    "args,test,stat,df,p",
    [
        ({"groups": SLEEP, "paired": True}, "paired_t", -4.062127683382037, 9, 0.002832890),
        ({"groups": SLEEP}, "welch_t", -1.860813467486853, 17.77647, 0.07939414),
        ({"groups": SLEEP, "test": "student_t"}, "student_t", -1.860813467486853, 18, 0.07918671),
        (groups(CORN, [0] * 15, paired=True, test="wilcoxon"), "wilcoxon", 24.0, None, 0.041259765625),
    ],
)
def test_published_values(args: dict[str, Any], test: str, stat: float, df: float | None, p: float) -> None:
    out = res(args)
    r = out["results"]
    assert out["test"] == test and r["statistic"] == pytest.approx(stat, rel=1e-6)
    assert r["p"] == pytest.approx(p, rel=1e-5) and (df is None or r["df"] == pytest.approx(df, rel=1e-5))


def test_auto_chooses_by_design_never_by_pretests() -> None:
    """Regression (review #27): Shapiro / Brown-Forsythe non-rejection picked Student t (p = .0013) where
    Welch gives .1171; pretest-selected inference rejected 13.8% under a true null."""
    rng = np.random.default_rng(10)
    out = res(groups(rng.normal(0, 3, 5), rng.normal(0, 1, 50)))
    assert out["test"] == "welch_t" and out["results"]["p"] == pytest.approx(0.117118, rel=1e-4)
    assert "non-rejection does not verify" in out["results"]["diagnostics"]["note"]
    paired = res({"groups": SLEEP, "paired": True})
    w = paired["results"]["diagnostics"]["normality"][0]
    assert (w["W"], w["p"]) == (pytest.approx(0.8298713, rel=1e-6), pytest.approx(0.03334161, rel=1e-5))
    assert paired["test"] == "paired_t" and any("Shapiro-Wilk rejects" in x for x in paired["warnings"])
    skew = [rng.lognormal(0, 1.5, 40) for _ in range(3)]
    assert res(groups(*skew))["test"] == "welch_anova"


def test_effects_follow_their_definitions() -> None:
    """Regression (review #28, #29): Welch carries the mean-difference CI (d_av has none); d_z is the
    uncorrected mean / SD of differences with a noncentral-t interval, defined for two pairs."""
    welch = res({"groups": SLEEP})["results"]
    assert welch["effect"]["name"] == "mean_difference" and welch["effect"]["value"] == pytest.approx(-1.58)
    assert welch["effect_secondary"]["name"] == "d_av" and welch["effect_secondary"]["ci"] is None
    cohen = res(groups(range(1, 8), [1, 3, 5, 7, 9, 11, 13], paired=True))["results"]["effect"]
    assert cohen["name"] == "d_z" and cohen["value"] == pytest.approx(-1.388730, rel=1e-6)
    two = res(groups([1, 3], [0, 0], paired=True))["results"]["effect"]
    assert two["value"] == pytest.approx(math.sqrt(2)) and two["ci"] == pytest.approx([-0.795928, 3.543968], rel=1e-5)
    student = res({"groups": SLEEP, "test": "student_t"})["results"]["effect"]
    assert student["name"] == "hedges_g" and student["ci"][0] < student["value"] < student["ci"][1]


def _exact_wilcoxon_p(d: list[float]) -> float:
    """Independent enumeration: all 2^n sign flips of the midranks, two-sided on W+."""
    nz = np.array([x for x in d if x != 0])
    r = stats.rankdata(np.abs(nz))
    obs = r[nz > 0].sum()
    center = r.sum() / 2
    sums = [sum(rk for rk, s in zip(r, signs, strict=True) if s) for signs in itertools.product((0, 1), repeat=len(r))]
    return float(np.mean([abs(s - center) >= abs(obs - center) - 1e-12 for s in sums]))


def test_small_tied_samples_get_exact_permutation_p() -> None:
    """Regression (review #31, #32): asymptotic p-values on tiny tied samples (.0369, .1939, .1017)."""
    assert res(groups([1] * 5, [0] * 5, paired=True, test="wilcoxon"))["results"]["p"] == pytest.approx(0.0625)
    assert res(groups([1, 1], [0, 0], test="mann_whitney"))["results"]["p"] == pytest.approx(1 / 3)
    kw = res(groups([0, 1], [2, 3], [4, 5], test="kruskal"))
    assert kw["results"]["p"] == pytest.approx(6 / 90) and "permutation" in kw["results"]["p_method"]
    sleep = res({"groups": SLEEP, "paired": True, "test": "wilcoxon"})["results"]  # ties and one zero
    diffs = [x - y for x, y in zip(DRUG1, DRUG2, strict=True)]
    assert sleep["p"] == pytest.approx(_exact_wilcoxon_p([round(x, 10) for x in diffs]))
    assert sleep["p_method"].startswith("exact permutation")


def test_differences_are_computed_once_at_the_stated_precision() -> None:
    """Regression (review #35): 0.825 - 0.775 and 0.375 - 0.325 differ by roundoff, hiding a tie."""
    a, b = [0.5, 0.825, 0.375, 0.5], [0.525, 0.775, 0.325, 0.55]
    loose = res(groups(a, b, paired=True, test="wilcoxon"))
    assert loose["results"]["statistic"] == 5.0 and any("decimals" in w for w in loose["warnings"])
    fixed = res(groups(a, b, paired=True, test="wilcoxon", decimals=3))["results"]
    supplied = res(groups([-0.025, 0.05, 0.05, -0.05], [0] * 4, paired=True, test="wilcoxon"))["results"]
    assert fixed["statistic"] == supplied["statistic"] == 4.0
    assert fixed["effect"]["value"] == pytest.approx(supplied["effect"]["value"]) == pytest.approx(0.2)


def test_tables_follow_fisher_and_cochran() -> None:
    tea = res({"table": [[3, 1], [1, 3]]})
    assert tea["test"] == "fisher" and tea["results"]["p"] == pytest.approx(0.4857143, rel=1e-6)
    assert tea["results"]["effect"]["value"] == pytest.approx(6.408309, rel=1e-5)  # R: conditional MLE
    big = res({"table": [[30, 10], [15, 25]]})["results"]
    assert big["statistic"] == pytest.approx(80 * (30 * 25 - 10 * 15) ** 2 / (40 * 40 * 45 * 35))
    assert big["effect"]["value"] == pytest.approx(math.sqrt(big["statistic"] / 80))  # phi for 2x2
    assert any("Cochran" in w for w in res({"table": [[1, 2, 0], [2, 1, 3]], "test": "chi_square"})["warnings"])


def test_anova_effect_sizes_by_hand_and_welch_without_inversion() -> None:
    g = [[1.0, 2.0, 3.0], [2.0, 3.0, 4.0], [5.0, 6.0, 7.0]]
    r = res(groups(*g, test="anova"))["results"]
    allv = np.concatenate(g)
    ss_b = sum(3 * (np.mean(x) - allv.mean()) ** 2 for x in g)
    ss_t = ((allv - allv.mean()) ** 2).sum()
    ms_w = (ss_t - ss_b) / 6
    assert r["effect"]["value"] == pytest.approx(ss_b / ss_t)
    assert r["effect_secondary"]["value"] == pytest.approx((ss_b - 2 * ms_w) / (ss_t + ms_w))
    assert r["apa"].startswith("F(2, 6) = ") and r["effect"]["ci"][0] <= r["effect"]["value"] <= r["effect"]["ci"][1]
    # Regression (review #34): a huge Welch F no longer fails an interval it never reports
    tight = res(groups(*[[m - 1e-4, m, m + 1e-4] for m in (0, 1, 2)], test="welch_anova"))["results"]
    assert tight["statistic"] == pytest.approx(257142857.14, rel=1e-6) and tight["effect"]["ci"] is None


@pytest.mark.parametrize(
    "args,message",
    [
        ({"groups": SLEEP, "table": [[1, 2], [3, 4]]}, "exactly one"),
        ({"groups": [*SLEEP, {"name": "x", "values": [1, 2]}], "paired": True}, "exactly 2 groups"),
        ({"groups": SLEEP, "test": "paired_t"}, "needs paired=true"),
        ({"groups": [*SLEEP, {"name": "x", "values": [1, 2]}], "test": "welch_t"}, "does not apply"),
        ({"groups": [{"name": "a", "values": [1, 2]}, {"name": "a", "values": [3, 4]}]}, "unique"),
        (groups([1, 2], [1, 2], paired=True, test="wilcoxon"), "all paired"),
        ({"table": [[1, 0], [2, 0]]}, "empty row or column"),
        ({"table": [[1, 2, 3], [2, 1, 3]], "test": "fisher"}, "2x2"),
        # review #33: degenerate inputs are input errors, not raw numerical / serialization failures
        (groups([1, 1], [2, 2], test="student_t"), "every group is constant"),
        (groups([1, 2, 3], [0, 1, 2], paired=True), "differences are constant"),
        (groups([1, 1], [1, 1], [1, 1]), "within-group variance"),
        (groups([1, 1], [2, 2], [3, 3], test="anova"), "every group is constant"),
        (groups([1, 1], [1, 1], test="mann_whitney"), "identical"),
    ],
)
def test_invalid_input(args: dict[str, Any], message: str) -> None:
    with pytest.raises(StatsInputError, match=message):
        compare.run(args)


def _coverage(draw: Any, truth: float, reps: int = 400) -> float:
    hits = 0
    for i in range(reps):
        ci = draw(np.random.default_rng(i))
        hits += ci is not None and ci[0] <= truth <= ci[1]
    return hits / reps


@pytest.mark.parametrize("delta,n", [(0.0, 10), (0.8, 15), (2.5, 8)])
def test_standardized_mean_difference_coverage(delta: float, n: int) -> None:
    g = _coverage(lambda r: effects.hedges_g(r.normal(delta, 1, n), r.normal(0, 1, n), 0.95)["ci"], delta)
    dz = _coverage(lambda r: effects.d_z(r.normal(delta, 1, n), 0.95)["ci"], delta)
    assert 0.92 <= g <= 0.98 and 0.92 <= dz <= 0.98, (g, dz)


@pytest.mark.parametrize("shift,n", [(0.0, 30), (1.0, 10), (2.0, 30), (3.0, 15)])
def test_rank_biserial_coverage_across_magnitudes(shift: float, n: int) -> None:
    """Regression (review #30): the Fisher-z interval covered 77% at shift 2; Newcombe method 5 holds."""
    truth = 2 * stats.norm.cdf(shift / math.sqrt(2)) - 1  # P(a > b) - P(a < b), N(shift, 1) vs N(0, 1)

    def draw(r: Any) -> Any:
        a, b = r.normal(shift, 1, n), r.normal(0, 1, n)
        return effects.rank_biserial_independent(a, b, float(stats.mannwhitneyu(a, b).statistic), 0.95)["ci"]

    assert 0.92 <= _coverage(draw, truth) <= 0.99


def test_eta_and_cramers_v_coverage() -> None:
    eta_true = np.var([0.0, 0.3, 0.6]) / (np.var([0.0, 0.3, 0.6]) + 1)

    def eta(r: Any) -> Any:
        gs = [r.normal(m, 1, 20) for m in (0.0, 0.3, 0.6)]
        return effects.eta_squared_ci(float(stats.f_oneway(*gs)[0]), 2, 57, 60, 0.95)

    joint = np.array([[0.20, 0.15, 0.10], [0.10, 0.15, 0.30]])
    outer = joint.sum(1, keepdims=True) * joint.sum(0, keepdims=True)
    v_true = math.sqrt(float(((joint - outer) ** 2 / outer).sum()))

    def v(r: Any) -> Any:
        t = r.multinomial(200, joint.ravel()).reshape(2, 3)
        chi2, _, dof, _ = stats.chi2_contingency(t, correction=False)
        return effects.cramers_v(float(chi2), int(dof), 200, 2, 0.95)["ci"]

    assert 0.92 <= _coverage(eta, eta_true) <= 0.98 and 0.92 <= _coverage(v, v_true) <= 0.98


@pytest.mark.parametrize("n1,n2,sd", [(6, 50, 5.0), (6, 6, 0.2), (10, 100, 5.0), (10, 40, 0.3)])
def test_rank_biserial_coverage_under_unequal_spread_and_imbalance(n1: int, n2: int, sd: float) -> None:
    """Regression (review #30, re-verification): Newcombe alone covered 76.5% / 80.3% here."""

    def draw(r: Any) -> Any:
        a, b = r.normal(0, sd, n1), r.normal(0, 1, n2)
        return effects.rank_biserial_independent(a, b, float(stats.mannwhitneyu(a, b).statistic), 0.95)["ci"]

    assert 0.92 <= _coverage(draw, 0.0) <= 0.995  # the union interval is conservative where both methods hold


@pytest.mark.parametrize("n1", [2, 3, 4, 5])
def test_rank_biserial_has_no_interval_below_the_validated_size(n1: int) -> None:
    """Regression (review #30, third pass): at n = 2 vs 50 with SD 5:1 the union covered 64.3%."""
    rng = np.random.default_rng(n1)
    a, b = rng.normal(0, 5, n1), rng.normal(0, 1, 50)
    eff = effects.rank_biserial_independent(a, b, float(stats.mannwhitneyu(a, b).statistic), 0.95)
    assert eff["value"] is not None and eff["ci"] is None and "no CI below 6" in str(eff["ci_note"])


def test_large_tied_rank_tests_use_monte_carlo_not_the_normal_approximation() -> None:
    """Regression (review #31): beyond exhaustive enumeration the normal approximation gave p = .0058 (exact
    .0848) and .0488 (exact .0768)."""
    mw = res(groups([1, 1], [1] * 40 + [0] * 160, test="mann_whitney"))["results"]
    w = res(groups([1] * 12 + [-1] * 4, [0] * 16, paired=True, test="wilcoxon"))["results"]
    assert mw["p"] == pytest.approx(0.0848, abs=3 * 0.0071) and "Monte Carlo" in mw["p_method"]
    # review #41: two-sided p = 2 q, so SE = 2 sqrt(q (1 - q) / B) <= 1 / sqrt(B)
    q = mw["p"] / 2
    assert mw["p_monte_carlo_se"]["bound"] == pytest.approx(1 / math.sqrt(20_000))
    assert mw["p_monte_carlo_se"]["estimate"] == pytest.approx(2 * math.sqrt(q * (1 - q) / 20_000))
    exact_w = _exact_wilcoxon_p([1.0] * 12 + [-1.0] * 4)
    assert w["p"] == pytest.approx(exact_w, abs=3 * 0.0071) and exact_w == pytest.approx(0.0768, abs=5e-4)


def test_paired_t_uses_the_same_rounded_differences() -> None:
    """Regression (review #38): decimals rounded the Wilcoxon differences but not the paired t's."""
    out = res(groups([1.04, 2.0, 3.3], [0.0, 1.0, 2.0], paired=True, decimals=1))["results"]
    rounded = np.array([1.0, 1.0, 1.3])
    assert out["statistic"] == pytest.approx(stats.ttest_1samp(rounded, 0).statistic)
    assert out["mean_difference"]["value"] == pytest.approx(rounded.mean())
    assert out["effect"]["value"] == pytest.approx(rounded.mean() / rounded.std(ddof=1))


def test_fisher_with_a_zero_cell_serializes() -> None:
    """Regression (review #39): the infinite conditional MLE broke JSON serialization."""
    out = res({"table": [[5, 0], [0, 5]]})
    eff = out["results"]["effect"]
    assert eff["value"] is None and eff["ci"][1] is None and eff["ci"][0] > 1 and out["results"]["p"] < 0.01


def test_anova_interval_scales_with_a_huge_f() -> None:
    """Regression (review #40): F = 3e8 failed the noncentrality search."""
    r = res(groups(*[[m - 1e-4, m, m + 1e-4] for m in (0, 1, 2)], test="anova"))["results"]
    assert r["statistic"] > 1e8 and r["effect"]["ci"] is not None and r["effect"]["ci"][0] > 0.99
    tighter = res(groups(*[[m - 1e-5, m, m + 1e-5] for m in (0, 1, 2)], test="anova"))["results"]
    assert (
        tighter["p"] < 1e-12 and tighter["effect"]["ci"] is None and "inversion failed" in tighter["effect"]["ci_note"]
    )


def test_sleep_mann_whitney_against_full_enumeration() -> None:
    """U = 25.5 as in R wilcox.test; R's p (.0693) is the normal approximation. With ties and C(20, 10) = 184756
    relabellings the tool uses a seeded Monte Carlo permutation p, checked here against the full enumeration."""
    out = res({"groups": SLEEP, "test": "mann_whitney"})["results"]
    ranks_all = stats.rankdata(DRUG1 + DRUG2)
    u_of = ranks_all[:10].sum() - 55
    center = 50.0
    sums = np.array([sum(ranks_all[list(c)]) - 55 for c in itertools.combinations(range(20), 10)])
    exact = float(np.mean(np.abs(sums - center) >= abs(u_of - center) - 1e-9))
    assert out["statistic"] == 25.5 and out["p"] == pytest.approx(exact, abs=3 * 0.0035)
