"""K9 Analyzer: designed-sweep statistics (ARCHITECTURE §6.5). Deterministic code only (no LLM).

anova(): OLS Type II ANOVA `y ~ C(a)*C(b)*... + C(seed)` on factorial points (center points excluded from the
model, used for the curvature test), Holm-adjusted p-values, partial eta^2, and the reporting gate:
an effect is "reported" only if p_holm < alpha, partial eta^2 >= min_eta2 and it exceeds the seed-noise floor.
Language: designed sweeps -> "effect within this setup" (not general causality).
"""

from __future__ import annotations

import math
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Effect:
    term: str
    df: float
    f: float | None
    p: float | None
    p_holm: float | None
    partial_eta2: float | None
    reported: bool
    statement: str


@dataclass
class AnovaResult:
    response: str
    n: int
    effects: list[Effect]
    curvature: dict[str, Any] | None
    seed_sigma: float | None
    notes: list[str] = field(default_factory=list)


def _finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def valid_rows(rows: list[dict[str, Any]], response: str) -> list[dict[str, Any]]:
    """Rows whose response is a finite number (failed runs report None/NaN and are excluded everywhere)."""
    return [r for r in rows if _finite(r.get(response))]


def seed_sigma(rows: list[dict[str, Any]], factors: list[str], response: str) -> float | None:
    """Pooled SD across seeds of identical configurations (the noise floor)."""
    groups: dict[tuple[Any, ...], list[float]] = {}
    for r in rows:
        groups.setdefault(tuple(r[f] for f in factors), []).append(float(r[response]))
    ss, dof = 0.0, 0
    for vals in groups.values():
        if len(vals) > 1:
            m = sum(vals) / len(vals)
            ss += sum((v - m) ** 2 for v in vals)
            dof += len(vals) - 1
    return math.sqrt(ss / dof) if dof else None


def winners_curse(sigma: float | None, n_configs: int) -> float | None:
    """Expected optimism of the best of N noisy configurations: about sigma * sqrt(2 ln N)."""
    if sigma is None or n_configs < 2:
        return None
    return sigma * math.sqrt(2 * math.log(n_configs))


def _center_weights(rows: list[dict[str, Any]], factors: list[str], cells: dict[tuple[Any, ...], list[float]],
                    scales: Mapping[str, str]) -> dict[tuple[Any, ...], float] | None:
    """Multilinear interpolation weights of the corner cells at the actual center coordinates, on each factor's
    design scale (log for log-sampled factors). Exact for any response that is linear (incl. interactions) on those
    scales, so a rounded integer center or a geometric center never turns a linear response into 'curvature'.
    None unless the design is a complete 2^k with one common center strictly inside every factor range."""
    try:
        levels = [sorted({float(c[i]) for c in cells}) for i in range(len(factors))]
        centers = {tuple(float(r[f]) for f in factors) for r in rows if r.get("is_center")}
    except (TypeError, ValueError):
        return None  # categorical factor: no center between levels
    if len(centers) != 1 or any(len(lv) != 2 for lv in levels) or len(cells) != 2 ** len(factors):
        return None
    (center,) = centers
    ts = []
    for f, c, (lo, hi) in zip(factors, center, levels, strict=True):
        log = scales.get(f) == "log"
        if log and min(lo, hi, c) <= 0:
            return None
        g = math.log if log else (lambda v: v)
        t = (g(c) - g(lo)) / (g(hi) - g(lo))
        if not 1e-9 < t < 1 - 1e-9:
            return None
        ts.append(t)
    return {cell: math.prod(t if float(v) == lv[1] else 1 - t for v, t, lv in zip(cell, ts, levels, strict=True))
            for cell in cells}


def _curvature(rows: list[dict[str, Any]], factors: list[str], response: str,
               scales: Mapping[str, str]) -> dict[str, Any] | None:
    """Center-vs-factorial curvature contrast: mean(center) minus the multilinear interpolation of the corner CELL
    means at the center coordinates (unequal replication does not tilt it), with a pure-error SE pooled from
    replicates within each cell and among the center points (corner differences are factor effects, not error);
    t test on the pure-error df (ADR-120, ADR-137)."""
    from scipy import stats

    center = [float(r[response]) for r in rows if r.get("is_center")]
    cells: dict[tuple[Any, ...], list[float]] = {}
    for r in rows:
        if not r.get("is_center"):
            cells.setdefault(tuple(r[f] for f in factors), []).append(float(r[response]))
    if len(center) < 2 or len(cells) < 2:
        return None
    weights = _center_weights(rows, factors, cells, scales)
    if weights is None:
        return None
    groups = [center, *cells.values()]
    ss = sum(sum((v - sum(g) / len(g)) ** 2 for v in g) for g in groups)
    dof = sum(len(g) - 1 for g in groups)
    if dof == 0 or ss == 0:
        return None
    predicted = sum(w * sum(cells[c]) / len(cells[c]) for c, w in weights.items())
    diff = sum(center) / len(center) - predicted
    se = math.sqrt(ss / dof) * math.sqrt(1 / len(center) + sum(w * w / len(cells[c]) for c, w in weights.items()))
    p = float(2 * stats.t.sf(abs(diff / se), dof))
    return {
        "center_minus_corners": diff,
        "p": p,
        "df": dof,
        "curvature": bool(p < 0.05),
        "statement": "curvature detected: the response at the center departs from linear interpolation between the "
        "tested levels (a curved response; not by itself evidence of an interior optimum)"
        if p < 0.05
        else "no evidence of curvature between the tested levels",
    }


def _fit(df: Any, factors: list[str], order: int) -> tuple[Any, list[str], bool]:
    """Type II ANOVA up to `order`. Terms whose columns are aliased with lower-order terms (e.g. an interaction whose
    cell is missing) are dropped, highest order first, until X has full column rank: OLS via pinv would otherwise
    return finite but meaningless numbers for them (NaN p-values alone do not detect aliasing; ADR-118).
    Returns (table, dropped term names, estimable = full rank with residual df left)."""
    import itertools

    import numpy as np
    import statsmodels.formula.api as smf
    from statsmodels.stats.anova import anova_lm

    terms = [":".join(f"C({f})" for f in c) for k in range(1, order + 1) for c in itertools.combinations(factors, k)]
    seed = " + C(seed)" if df["seed"].nunique() > 1 else ""
    dropped: list[str] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        while True:
            fit = smf.ols(f"y ~ {' + '.join(terms)}{seed}", data=df).fit()
            x = fit.model.exog
            rank = np.linalg.matrix_rank(x)
            if rank == x.shape[1] or len(terms) <= 1:
                break
            cols = [re.sub(r"\[[^\]]*\]", "", n) for n in fit.model.exog_names]  # "C(a)[T.1]:C(b)[T.2]" -> term
            for t in reversed(terms):  # the highest-order term that adds no rank is the aliased one
                keep = np.array([c != t for c in cols])
                if np.linalg.matrix_rank(x[:, keep]) == rank:
                    terms.remove(t)
                    dropped.append(t.replace("C(", "").replace(")", ""))
                    break
            else:
                break
        estimable = bool(rank == x.shape[1] and fit.df_resid > 0)
        return anova_lm(fit, typ=2) if estimable else None, dropped, estimable


def _full_rank(fit: Any) -> bool:
    import numpy as np

    x = fit.model.exog
    return bool(np.linalg.matrix_rank(x) == x.shape[1] and fit.df_resid > 0)


def _missing_cells(df: Any, factors: list[str]) -> list[str]:
    import itertools

    levels = [sorted(df[f].unique()) for f in factors]
    have = {tuple(r) for r in df[factors].itertuples(index=False)}
    return [
        ", ".join(f"{f}={v}" for f, v in zip(factors, combo, strict=True))
        for combo in itertools.product(*levels)
        if tuple(combo) not in have
    ]


def _statement(name: str, rep: bool, ph: float, eta: float | None, alpha: float, min_eta2: float, size: float,
               sigma: float | None) -> str:
    """Why a term is (not) reported; a policy threshold is never worded as 'indistinguishable from noise'."""
    if rep:
        return f"{name}: effect within this setup (p_holm={ph:.3g}, partial eta^2={eta:.2f})"
    if not ph < alpha:
        return f"{name}: not distinguishable from noise (p_holm={ph:.3g})"
    if eta is None or eta < min_eta2:
        return f"{name}: significant (p_holm={ph:.3g}) but below the minimum effect size (partial eta^2 < {min_eta2})"
    return f"{name}: significant (p_holm={ph:.3g}) but smaller than the seed-noise floor ({size:.3g} <= sd {sigma:.3g})"


def anova(
    rows: list[dict[str, Any]], factors: list[str], response: str, *, alpha: float = 0.05, min_eta2: float = 0.06,
    scales: Mapping[str, str] | None = None,
) -> AnovaResult:
    """Fits the full factorial model; if cells are missing (e.g. failed runs) it falls back to lower-order models.
    Terms that cannot be estimated are reported as such (never as 'no effect')."""
    import pandas as pd
    from statsmodels.stats.multitest import multipletests

    ok = valid_rows(rows, response)
    corner = [r for r in ok if not r.get("is_center")]
    sigma = seed_sigma(corner, factors, response)
    df = pd.DataFrame(
        [{**{f: str(r[f]) for f in factors}, "seed": str(r.get("seed", 0)), "y": float(r[response])} for r in corner]
    )
    notes: list[str] = []
    if len(ok) < len(rows):
        notes.append(f"{len(rows) - len(ok)} run(s) without a finite response excluded from all statistics")
    missing = _missing_cells(df, factors)
    aliased: list[str] = []
    table, order = None, len(factors)
    while order >= 1:
        table, aliased, full_rank = _fit(df, factors, order)
        if full_rank and (
            not table.drop(index=[t for t in table.index if t.startswith(("Residual", "C(seed)"))])["PR(>F)"]
            .isna()
            .any()
        ):
            break
        order -= 1
    if missing:
        notes.append(f"missing design cells (failed runs): {missing}; terms above order {order} are not estimable")
    # Exhausted fallback: never infer from the last invalid candidate.
    table = table if order else None
    aliased = aliased if order else []
    resid_ss = float(table.loc["Residual", "sum_sq"]) if table is not None else 0.0
    terms = [t for t in table.index if t != "Residual" and not t.startswith("C(seed)")] if table is not None else []
    pvals = [float(table.loc[t, "PR(>F)"]) for t in terms] if table is not None else []
    adj = list(multipletests(pvals, method="holm")[1]) if pvals else []
    effects = []
    for t, p, ph in zip(terms, pvals, adj, strict=True):
        assert table is not None  # A missing table produces no terms.
        ss = float(table.loc[t, "sum_sq"])
        eta = ss / (ss + resid_ss) if ss + resid_ss > 0 else None
        name = t.replace("C(", "").replace(")", "")
        size = 2 * math.sqrt(ss / len(df))  # effect in response units (= high-low mean difference, balanced 2 levels)
        above_noise = sigma is None or size > sigma
        rep = bool(ph < alpha and eta is not None and eta >= min_eta2 and above_noise)
        effects.append(
            Effect(
                name,
                float(table.loc[t, "df"]),
                _f(table.loc[t, "F"]),
                _f(p),
                float(ph),
                eta,
                rep,
                _statement(name, rep, ph, eta, alpha, min_eta2, size, sigma),
            )
        )
    import itertools

    above = [":".join(c) for k in range(order + 1, len(factors) + 1) for c in itertools.combinations(factors, k)]
    if aliased:
        notes.append(f"aliased with lower-order terms (not estimable): {aliased}")
    for name in dict.fromkeys(sorted(aliased, key=lambda t: t.count(":")) + above):
        effects.append(
            Effect(
                name,
                0,
                None,
                None,
                None,
                None,
                False,
                f"{name}: NOT ESTIMABLE from this design (missing cells or aliased); see failure rules",
            )
        )
    if sigma is None:
        notes.append("no replicate seeds: the seed-noise floor is unavailable and was not applied")
    if df["seed"].nunique() < 3:
        notes.append("fewer than 3 seeds per configuration: low power; interaction claims need >= 5 seeds")
    notes.append("seeds capture initialization/shuffle randomness only, not subject sampling variance")
    curvature = _curvature(ok, factors, response, scales or {})
    notes.extend(["curvature test not applicable: requires a complete design, replicated center and pure error, "
                  "and a common interior center on valid scales (positive coordinates for log scales)"]
                 if curvature is None else [])
    return AnovaResult(response, len(df), effects, curvature, sigma, notes)


def surface(
    rows: list[dict[str, Any]],
    factors: list[str],
    response: str,
    *,
    log_factors: tuple[str, ...] = (),
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Exploratory response-surface regression over ALL successful runs (screen + adaptive search):
    standardized factors, quadratic terms and pairwise interactions. Adaptive samples => ASSOCIATION, not effect."""
    import itertools

    import numpy as np
    import pandas as pd
    import statsmodels.formula.api as smf
    from statsmodels.stats.multitest import multipletests

    ok = valid_rows(rows, response)
    df = pd.DataFrame(
        [
            {f: (math.log10(float(r[f])) if f in log_factors else float(r[f])) for f in factors}
            | {"y": float(r[response])}
            for r in ok
        ]
    )
    varying = [f for f in factors if df[f].std() > 0]
    # a quadratic term needs >= 3 distinct levels: with 2 levels the standardized square is constant (aliased
    # with the intercept) and OLS would return an arbitrary coefficient and p-value (ADR-118)
    curved = [f for f in varying if df[f].nunique() >= 3]
    for f in varying:
        df[f] = (df[f] - df[f].mean()) / df[f].std()
    terms = varying + [f"I({f}**2)" for f in curved] + [f"{a}:{b}" for a, b in itertools.combinations(varying, 2)]
    linear_only = [f for f in varying if f not in curved]
    if len(df) <= len(terms) + 2:
        return {"n": len(df), "terms": [], "note": "too few successful runs for a surface fit"}
    fit = smf.ols("y ~ " + " + ".join(terms), data=df).fit()
    if not _full_rank(fit):
        return {
            "n": len(df),
            "terms": [],
            "linear_only": linear_only,
            "note": "surface terms are not separately estimable from these runs (aliased design)",
        }
    names = [t for t in fit.params.index if t != "Intercept"]
    adj = multipletests([float(fit.pvalues[t]) for t in names], method="holm")[1]
    out = [
        {"term": t, "coef": float(fit.params[t]), "p_holm": float(ph), "associated": bool(ph < alpha)}
        for t, ph in zip(names, adj, strict=True)
    ]
    return {
        "n": len(df),
        "r2": float(fit.rsquared),
        "terms": out,
        "fixed": [f for f in factors if f not in varying],
        "linear_only": linear_only,
        "note": "exploratory association over adaptively sampled runs (not a designed-effect claim)",
        "residual_sd": float(np.sqrt(fit.scale)),
    }


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v
