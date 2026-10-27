"""stats_compare_groups: group comparison chosen by estimand and design, never by assumption pretests (review
#27: pretest-selected Student t rejected 13.8% under a true null vs 5.2% for Welch). Defaults: independent means ->
Welch t / Welch ANOVA; paired means -> paired t. Rank tests answer a different question (stochastic ordering /
location of differences) and run only when requested. Shapiro-Wilk and Brown-Forsythe are reported as diagnostics:
non-rejection does not verify an assumption. Post-hoc / multiple comparisons: pass p-values to stats_multiplicity."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Any

import numpy as np
from researchos.contracts.gateway import ToolBinding, ToolContext
from scipy import stats
from statsmodels.stats.oneway import anova_oneway

from . import effects, ranks, tables
from ._common import CONFIDENCE_SCHEMA, Report, StatsInputError, apa, as_int, confidence_of, finite_vector, tool_spec

NAME = "stats_compare_groups"
TWO = ("welch_t", "student_t", "paired_t", "mann_whitney", "wilcoxon")
MANY = ("welch_anova", "anova", "kruskal")
PAIRED = ("paired_t", "wilcoxon")
MAX_GROUPS, MAX_VALUES = 10, 5000
REFS = {
    "student_t": "Student (1908) Biometrika 6:1-25",
    "welch_t": "Welch BL (1947) Biometrika 34:28-35",
    "paired_t": "Student (1908) Biometrika 6:1-25 (paired differences)",
    "mann_whitney": "Mann HB, Whitney DR (1947) Ann Math Stat 18:50-60",
    "wilcoxon": "Wilcoxon F (1945) Biometrics Bull 1:80-83",
    "anova": "Fisher RA (1925) Statistical Methods for Research Workers",
    "welch_anova": "Welch BL (1951) Biometrika 38:330-336",
    "kruskal": "Kruskal WH, Wallis WA (1952) J Am Stat Assoc 47:583-621",
    "diagnostics": "Shapiro SS, Wilk MB (1965) Biometrika 52:591-611; Brown MB, Forsythe AB (1974) JASA 69:364-367; "
    "against pretest-based selection: Zimmerman DW (2004) Br J Math Stat Psychol 57:173-181; Rasch D, Kubinger KD, "
    "Moder K (2011) Stat Papers 52:219-231",
}
RULE = (
    f"{tables.RULE} Numeric, test=auto, by design only: 2 paired groups -> paired t; 2 independent groups -> Welch t; "
    "3+ groups -> Welch ANOVA. Student t / ANOVA (equal variances) and rank tests (Mann-Whitney, Wilcoxon, "
    "Kruskal-Wallis: a different null hypothesis) only when requested. Diagnostics never change the test."
)
SPEC = tool_spec(
    NAME,
    "Compare groups. test=auto picks by design (Welch t, paired t, Welch ANOVA for numeric groups; Fisher or "
    "chi-square for a table); other tests on request. Numeric outcome: groups=[{name, values}] (2-10 groups; "
    "paired=true for 2 aligned groups; decimals = measurement precision of paired differences). Categorical outcome: "
    "table = contingency counts. Returns statistic, df, p and its method, an effect size (CI only where validated), "
    "diagnostics (Shapiro-Wilk, Brown-Forsythe) and an APA string. Read-only.",
    {
        "groups": {
            "type": "array",
            "minItems": 2,
            "maxItems": MAX_GROUPS,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "values": {"type": "array", "minItems": 2, "maxItems": MAX_VALUES, "items": {"type": "number"}},
                },
                "required": ["name", "values"],
                "additionalProperties": False,
            },
        },
        "paired": {"type": "boolean", "default": False},
        "decimals": {
            "type": "integer",
            "minimum": 0,
            "maximum": 12,
            "description": "round paired differences once to this measurement precision (ties, Wilcoxon)",
        },
        "table": {
            "type": "array",
            "minItems": 2,
            "maxItems": 20,
            "items": {"type": "array", "minItems": 2, "maxItems": 20, "items": {"type": "integer", "minimum": 0}},
        },
        "test": {"enum": ["auto", *TWO, *MANY, *tables.TESTS], "default": "auto"},
        "alpha": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 0.2,
            "default": 0.05,
            "description": "level at which diagnostics are flagged (never changes the test)",
        },
        "seed": {
            "type": "integer",
            "minimum": 0,
            "maximum": 2**63 - 1,
            "default": 0,
            "description": "seed of a Monte Carlo permutation p (Kruskal-Wallis, large tables only)",
        },
        "confidence": CONFIDENCE_SCHEMA,
    },
)


def _df(v: float) -> str:
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.2f}"


def _groups(args: Mapping[str, Any]) -> tuple[list[str], list[np.ndarray[Any, Any]]]:
    raw = args.get("groups")
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_GROUPS:
        raise StatsInputError(f"groups must be a list of 2-{MAX_GROUPS} objects {{name, values}}")
    names = [str(g.get("name", "")) if isinstance(g, Mapping) else "" for g in raw]
    if "" in names or len(set(names)) != len(names):
        raise StatsInputError("every group needs a unique non-empty name")
    values = [
        finite_vector(g["values"], f"groups[{n}].values", min_len=2, max_len=MAX_VALUES)
        for g, n in zip(raw, names, strict=True)
    ]
    return names, values


def diagnostics(samples: dict[str, Any], independent: list[Any], alpha: float, rep: Report) -> dict[str, Any]:
    normality: list[dict[str, Any]] = []
    for label, v in samples.items():
        w = p = None
        if len(v) >= 3 and np.ptp(v) > 0:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # near-constant samples: roundoff in W
                w, p = (float(x) for x in stats.shapiro(v))
        normality.append({"sample": label, "n": len(v), "W": w, "p": p})
    flagged = [x["sample"] for x in normality if x["p"] is not None and x["p"] < alpha]
    if flagged:
        rep.warn(
            f"Shapiro-Wilk rejects normality for {flagged} at {alpha}: mean-based inference then relies on "
            "approximate normality of the means; a rank test answers a different question (request it explicitly)"
        )
    out: dict[str, Any] = {"normality": normality, "note": "diagnostics only; non-rejection does not verify normality"}
    if len(independent) >= 2:
        stat, p = stats.levene(*independent, center="median")
        out["equal_variance_brown_forsythe"] = {"statistic": float(stat), "p": float(p)} if np.isfinite(p) else None
    return out


def _check_spread(test: str, values: list[Any], d: Any) -> None:
    """Reject inputs on which the requested test is undefined (review #33)."""
    if test == "paired_t" and np.std(d) == 0:
        raise StatsInputError("the paired differences are constant: the paired t-test is undefined")
    if test in ("student_t", "welch_t", "anova") and all(np.ptp(v) == 0 for v in values):
        raise StatsInputError(f"every group is constant: {test} is undefined (no within-group variance)")
    if test == "welch_anova" and any(np.ptp(v) == 0 for v in values):
        raise StatsInputError("welch_anova needs within-group variance in every group (weights are n / s^2)")


def mean_two(test: str, a: Any, b: Any, d: Any, conf: float) -> dict[str, Any]:
    """`d`: the paired differences computed once (and rounded to `decimals`), shared with Wilcoxon (review #38)."""
    if test == "paired_t":
        r, eff = stats.ttest_1samp(d, 0.0), effects.d_z(d, conf)
    elif test == "student_t":
        r, eff = stats.ttest_ind(a, b, equal_var=True), effects.hedges_g(a, b, conf)
    else:
        r = stats.ttest_ind(a, b, equal_var=False)
    ci = r.confidence_interval(conf)
    diff = float(np.mean(d)) if test == "paired_t" else float(a.mean() - b.mean())
    mean_diff = effects.effect("mean_difference", diff, [float(ci.low), float(ci.high)], ref=REFS[test])
    out = {"statistic": float(r.statistic), "df": float(r.df), "p": float(r.pvalue), "mean_difference": mean_diff}
    if test == "welch_t":  # review #28: no pooled-model standardized CI under unequal variances
        eff, out["effect_secondary"] = mean_diff, effects.d_av(a, b)
    out["effect"] = eff
    out["apa"] = apa("Welch's t" if test == "welch_t" else "t", _df(r.df), r.statistic, r.pvalue, eff, conf)
    return out


def mean_many(test: str, values: list[Any], conf: float) -> dict[str, Any]:
    n, k = sum(len(v) for v in values), len(values)
    eta, omega = effects.ss_effects(values)
    if test == "welch_anova":
        r = anova_oneway(values, use_var="unequal")
        f, p, df1, df2 = float(r.statistic), float(r.pvalue), float(r.df_num), float(r.df_denom)
        eff = effects.effect("eta_squared", eta, None, "descriptive; no interval under unequal variances")
    else:
        f, p = (float(x) for x in stats.f_oneway(*values))
        df1, df2 = k - 1, n - k
        ci = effects.eta_squared_ci(f, k - 1, n - k, n, conf)
        eff = effects.effect("eta_squared", eta, ci, None if ci else "noncentral F inversion failed numerically")
    symbol = "Welch's F" if test == "welch_anova" else "F"
    return {
        "statistic": f,
        "df": [df1, df2],
        "p": p,
        "effect": eff,
        "effect_secondary": effects.effect("omega_squared", omega, None, "point estimate"),
        "apa": apa(symbol, f"{_df(df1)}, {_df(df2)}", f, p, eff, conf),
    }


def _test_of(args: Mapping[str, Any], k: int, paired: bool) -> str:
    test = str(args.get("test", "auto"))
    if test == "auto":
        return "paired_t" if paired else ("welch_t" if k == 2 else "welch_anova")
    if test in tables.TESTS:
        raise StatsInputError(f"{test} needs a contingency table")
    if paired != (test in PAIRED):
        raise StatsInputError(f"{test} {'does not take' if paired else 'needs'} paired=true")
    if (k == 2) != (test in TWO):
        raise StatsInputError(f"{test} does not apply to {k} groups")
    return test


def _dispatch(
    test: str, values: list[Any], d: Any, args: Mapping[str, Any], conf: float, rep: Report
) -> dict[str, Any]:
    _check_spread(test, values, d)
    seed = as_int(args.get("seed", 0), "seed", maximum=2**63 - 1)
    if test == "wilcoxon":
        return ranks.wilcoxon(d, rep, seed) | {"apa_symbol": "V"}
    if test == "mann_whitney":
        return ranks.mann_whitney(values[0], values[1], conf, seed) | {"apa_symbol": "U"}
    if test == "kruskal":
        return ranks.kruskal(values, seed, rep) | {"apa_symbol": "H"}
    return mean_two(test, values[0], values[1], d, conf) if test in TWO else mean_many(test, values, conf)


def run(args: Mapping[str, Any]) -> str:
    conf = confidence_of(args)
    if ("table" in args) == ("groups" in args):
        raise StatsInputError("give exactly one of groups (numeric outcome) or table (contingency counts)")
    if "table" in args:
        if str(args.get("test", "auto")) not in ("auto", *tables.TESTS):
            raise StatsInputError(f"a table needs test auto, {' or '.join(tables.TESTS)}")
        return tables.run_table(NAME, args, str(args.get("test", "auto")), conf)
    names, values = _groups(args)
    paired = bool(args.get("paired", False))
    if paired and (len(values) != 2 or len(values[0]) != len(values[1])):
        raise StatsInputError("paired=true needs exactly 2 groups of equal length (aligned pairs)")
    test = _test_of(args, len(values), paired)
    d: Any = values[0] - values[1] if paired else None
    if paired and "decimals" in args:
        d = np.round(d, as_int(args["decimals"], "decimals", maximum=12))
    rep = Report(NAME, test, f"{REFS[test]}; diagnostics: {REFS['diagnostics']}", sum(len(v) for v in values))
    rep.assumptions += ["Observations are independent " + ("across pairs." if paired else "within and between groups.")]
    result = _dispatch(test, values, d, args, conf, rep)
    if not (np.isfinite(result["statistic"]) and np.isfinite(result["p"])):
        raise StatsInputError(f"{test} is numerically undefined for these data (non-finite statistic or p)")
    symbol = result.pop("apa_symbol", None)
    if symbol:
        result["apa"] = apa(
            symbol,
            _df(result["df"]) if "df" in result else "",
            result["statistic"],
            result["p"],
            result["effect"],
            conf,
        )
    found = diagnostics(
        {"differences": d} if paired else dict(zip(names, values, strict=True)),
        [] if paired else values,
        float(args.get("alpha", 0.05)),
        rep,
    )
    descriptive = [
        {"name": n, "n": len(v), "mean": float(v.mean()), "sd": float(v.std(ddof=1)), "median": float(np.median(v))}
        for n, v in zip(names, values, strict=True)
    ]
    return rep.emit({**result, "groups": descriptive, "diagnostics": found}, test=test, rule=RULE, confidence=conf)


def _impl(args: dict[str, Any], ctx: ToolContext) -> str:
    return run(args)


BINDING = ToolBinding(spec=SPEC, impl=_impl)
