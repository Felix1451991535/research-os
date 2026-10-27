"""Binomial proportion intervals and likelihood-ratio intervals."""

from __future__ import annotations

import math

from scipy import stats

from ._common import interval, z_crit

WILSON_REF = "Wilson EB (1927) J Am Stat Assoc 22:209-212 (score interval, no continuity correction)"
CLOPPER_PEARSON_REF = "Clopper CJ, Pearson ES (1934) Biometrika 26:404-413 (exact, beta quantiles)"
LR_REF = "Simel DL, Samsa GP, Matchar DB (1991) J Clin Epidemiol 44:763-770 (log method)"


def wilson(k: int, n: int, confidence: float) -> list[float] | None:
    """Wilson score interval (same algebra as statsmodels proportion_confint(method='wilson'))."""
    if n == 0:
        return None
    z = z_crit(confidence)
    z2, p = z * z, k / n
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return interval(max(0.0, center - half), min(1.0, center + half))


def clopper_pearson(k: int, n: int, confidence: float) -> list[float] | None:
    """Exact interval from beta quantiles; lower = 0 when k = 0, upper = 1 when k = n."""
    if n == 0:
        return None
    a2 = (1.0 - confidence) / 2.0
    lo = 0.0 if k == 0 else float(stats.beta.ppf(a2, k, n - k + 1))
    hi = 1.0 if k == n else float(stats.beta.isf(a2, k + 1, n - k))
    return interval(lo, hi)


def log_ratio_ci(value: float, a: int, n_a: int, b: int, n_b: int, confidence: float) -> list[float] | None:
    """CI for a ratio of proportions (a/n_a)/(b/n_b): exp(ln r +/- z*sqrt(1/a - 1/n_a + 1/b - 1/n_b)).
    None when any cell is 0 (log undefined)."""
    if min(a, b, n_a, n_b) == 0 or value <= 0:
        return None
    se = math.sqrt(1.0 / a - 1.0 / n_a + 1.0 / b - 1.0 / n_b)
    z = z_crit(confidence)
    return interval(math.exp(math.log(value) - z * se), math.exp(math.log(value) + z * se))
