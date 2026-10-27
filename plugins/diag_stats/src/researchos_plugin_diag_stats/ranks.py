"""Rank tests for stats_compare_groups with a reported p-value method (review #31, #32, #35).

p-values, in order: without ties or zeros and n <= MAX_EXACT_N per group, SciPy's exact null distribution; with ties
or zeros, the exhaustive permutation distribution when the distinct relabellings / sign flips number at most
MAX_EXACT; else, while the smaller group (Mann-Whitney) or the nonzero differences (Wilcoxon) number fewer than
MAX_EXACT_N, a seeded Monte Carlo permutation p (review #31: the normal approximation gave .0058 for an exact
.0848); only beyond that the normal approximation with tie and continuity correction. Kruskal-Wallis: chi-square
approximation when every group has >= 5, else exhaustive or seeded Monte Carlo permutation.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import stats

from . import effects
from ._common import Report, StatsInputError

MAX_EXACT = 20_000
MAX_EXACT_N = 50
MC_RESAMPLES = 20_000
EXACT = "exact permutation distribution (exhaustive; ties and zeros handled)"
UNTIED = "exact null distribution (no ties)"
APPROX = "normal approximation, tie + continuity corrected"


MC = f"Monte Carlo permutation ({MC_RESAMPLES} resamples, seed {{seed}})"


def monte_carlo(seed: int) -> tuple[Any, str]:
    return stats.PermutationMethod(n_resamples=MC_RESAMPLES, rng=np.random.default_rng(seed)), MC.format(seed=seed)


def mc_error(p: float, two_sided: bool) -> dict[str, float]:
    """Monte Carlo standard error of a permutation p (review #41). Two-sided p = 2 q (q = smaller tail), so
    SE = 2 sqrt(q (1 - q) / B) <= 1 / sqrt(B); one-sided SE = sqrt(p (1 - p) / B) <= 0.5 / sqrt(B)."""
    if two_sided:
        q = min(p / 2.0, 0.5)
        return {"estimate": 2.0 * math.sqrt(q * (1 - q) / MC_RESAMPLES), "bound": 1.0 / math.sqrt(MC_RESAMPLES)}
    return {"estimate": math.sqrt(p * (1 - p) / MC_RESAMPLES), "bound": 0.5 / math.sqrt(MC_RESAMPLES)}


def mann_whitney(a: Any, b: Any, conf: float, seed: int) -> dict[str, Any]:
    if np.ptp(np.concatenate([a, b])) == 0:
        raise StatsInputError("all values are identical: the Mann-Whitney test is undefined")
    both = np.concatenate([a, b])
    method: Any
    if len(np.unique(both)) == len(both) and max(len(a), len(b)) <= MAX_EXACT_N:
        method, label = "exact", UNTIED
    elif math.comb(len(a) + len(b), len(a)) <= MAX_EXACT:
        method, label = stats.PermutationMethod(n_resamples=MAX_EXACT), EXACT
    elif min(len(a), len(b)) < MAX_EXACT_N:
        method, label = monte_carlo(seed)
    else:
        method, label = "asymptotic", APPROX
    r = stats.mannwhitneyu(a, b, alternative="two-sided", method=method, use_continuity=True)
    out = {
        "statistic": float(r.statistic),
        "statistic_name": "U (group 1)",
        "p": float(r.pvalue),
        "p_method": label,
        "effect": effects.rank_biserial_independent(a, b, float(r.statistic), conf),
    }
    return out | ({"p_monte_carlo_se": mc_error(float(r.pvalue), True)} if label.startswith("Monte Carlo") else {})


def near_ties(d: Any) -> bool:
    """Distinct |d| values closer than float roundoff of their magnitude (e.g. 0.05 vs 0.05000000000000004)."""
    v = np.unique(np.abs(d[d != 0]))
    return bool(len(v) > 1 and (np.diff(v) <= 1e-9 * v[1:]).any())


def wilcoxon(d: Any, rep: Report, seed: int) -> dict[str, Any]:
    """Signed-rank test on the differences `d`, computed once by the caller (review #35)."""
    nz = d[d != 0]
    if (d == 0).any():
        rep.warn(f"{int((d == 0).sum())} zero differences dropped (Wilcoxon zero_method='wilcox')")
    if len(nz) == 0:
        raise StatsInputError("all paired differences are zero: the Wilcoxon test is undefined")
    if near_ties(d):
        rep.warn(
            "differences that agree up to float roundoff are treated as distinct; set `decimals` to the "
            "measurement precision to make them ties"
        )
    untied = len(np.unique(np.abs(nz))) == len(nz) and len(nz) == len(d)
    if untied and len(nz) <= MAX_EXACT_N:
        r, label = stats.wilcoxon(nz, method="exact"), UNTIED
    elif 2 ** len(nz) <= MAX_EXACT:
        r, label = stats.wilcoxon(nz, method=stats.PermutationMethod(n_resamples=MAX_EXACT)), EXACT
    elif len(nz) < MAX_EXACT_N:
        method, label = monte_carlo(seed)
        r = stats.wilcoxon(nz, method=method)
    else:
        r, label = stats.wilcoxon(nz, method="approx", correction=True), APPROX
    out = {
        "statistic": float(r.statistic),
        "statistic_name": "min(W+, W-)",
        "p": float(r.pvalue),
        "p_method": label,
        "effect": effects.rank_biserial_paired(d),
    }
    return out | ({"p_monte_carlo_se": mc_error(float(r.pvalue), True)} if label.startswith("Monte Carlo") else {})


def _relabellings(sizes: list[int]) -> int:
    count = math.factorial(sum(sizes))
    for k in sizes:
        count //= math.factorial(k)
    return count


def kruskal(values: list[Any], seed: int, rep: Report) -> dict[str, Any]:
    if np.ptp(np.concatenate(values)) == 0:
        raise StatsInputError("all values are identical: the Kruskal-Wallis test is undefined")
    h = float(stats.kruskal(*values).statistic)
    sizes = [len(v) for v in values]
    if min(sizes) >= 5:
        p, p_method = float(stats.chi2.sf(h, len(values) - 1)), "chi-square approximation (all groups >= 5)"
    else:
        exact = _relabellings(sizes) <= MAX_EXACT
        res = stats.permutation_test(
            values,
            lambda *s: stats.kruskal(*s).statistic,
            permutation_type="independent",
            alternative="greater",
            n_resamples=MAX_EXACT if exact else MC_RESAMPLES,
            rng=np.random.default_rng(seed),
        )
        p = float(res.pvalue)
        p_method = EXACT if exact else MC.format(seed=seed)
        rep.warn("a group has < 5 observations: the chi-square approximation is unreliable; permutation p reported")
    out = {
        "statistic": h,
        "df": len(values) - 1,
        "p": p,
        "p_method": p_method,
        "effect": effects.epsilon_squared(h, sum(sizes)),
    }
    return out | ({"p_monte_carlo_se": mc_error(p, False)} if p_method.startswith("Monte Carlo") else {})
