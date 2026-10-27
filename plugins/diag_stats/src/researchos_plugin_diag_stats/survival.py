"""stats_survival: Kaplan-Meier (Greenwood variance, log(-log) CI), log-rank test, Cox proportional hazards (Efron
ties) with a Schoenfeld-residual PH check, and Harrell's C for given risk scores. Uses lifelines (MIT; optional extra
`survival`). Right-censored data only; no competing risks, no time-varying covariates. Research use only."""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping
from typing import Any

import numpy as np
from researchos.contracts.gateway import ToolBinding, ToolContext

from ._common import CONFIDENCE_SCHEMA, Report, StatsInputError, binary_vector, confidence_of, finite_vector, tool_spec

NAME = "stats_survival"
ANALYSES = ("km", "logrank", "cox", "concordance")
MAX_N, MAX_COVARIATES, MAX_GROUPS, MAX_TIMES, MAX_CURVE = 5000, 20, 10, 50, 200
REFS = {
    "km": "Kaplan EL, Meier P (1958) JASA 53:457-481; Greenwood M (1926); log(-log) CI: Kalbfleisch JD, Prentice RL "
    "(1980) The Statistical Analysis of Failure Time Data",
    "logrank": "Mantel N (1966) Cancer Chemother Rep 50:163-170; Peto R, Peto J (1972) JRSS A 135:185-207",
    "cox": "Cox DR (1972) JRSS B 34:187-220; Efron B (1977) JASA 72:557-565 (ties); PH check: Grambsch PM, Therneau "
    "TM (1994) Biometrika 81:515-526; events per variable: Peduzzi P et al. (1996) J Clin Epidemiol 49:1373-1379",
    "concordance": "Harrell FE et al. (1982) JAMA 247:2543-2546 (C-index; tied risk scores count 1/2)",
}
_ARRAY = {"type": "array", "minItems": 2, "maxItems": MAX_N}
SPEC = tool_spec(
    NAME,
    "Survival analysis of right-censored data. durations (>= 0) and events (1 = event, 0 = censored). analysis=km: "
    "Kaplan-Meier per group with Greenwood log(-log) CI, medians and survival at `times`; logrank: test across "
    "`groups`; cox: Cox PH (Efron ties) on `covariates` {name: values} with hazard ratios, likelihood-ratio test, "
    "concordance and a Schoenfeld PH check; concordance: Harrell's C of `risk_scores` (higher = earlier event). "
    "Read-only.",
    {
        "analysis": {"enum": list(ANALYSES)},
        "durations": {**_ARRAY, "items": {"type": "number", "minimum": 0}},
        "events": {**_ARRAY, "items": {"type": "integer", "enum": [0, 1]}},
        "groups": {**_ARRAY, "items": {"type": ["string", "integer"]}},
        "covariates": {
            "type": "object",
            "minProperties": 1,
            "maxProperties": MAX_COVARIATES,
            "additionalProperties": {**_ARRAY, "items": {"type": "number"}},
        },
        "risk_scores": {**_ARRAY, "items": {"type": "number"}},
        "times": {"type": "array", "minItems": 1, "maxItems": MAX_TIMES, "items": {"type": "number", "minimum": 0}},
        "confidence": CONFIDENCE_SCHEMA,
    },
    ("analysis", "durations", "events"),
)


def lifelines() -> Any:
    try:
        import lifelines as ll
    except ImportError as exc:  # optional extra
        raise StatsInputError(
            'stats_survival needs lifelines: install "researchos-plugin-diag_stats[survival]"'
        ) from exc
    return ll


def _dof(result: Any) -> int:
    """lifelines names the attribute differently across result objects."""
    return int(getattr(result, "degrees_of_freedom", None) or result.degrees_freedom)


def _num(v: Any) -> float | None:
    f = float(v)
    return f if math.isfinite(f) else None


def _base(args: Mapping[str, Any]) -> tuple[Any, Any]:
    t = finite_vector(args.get("durations"), "durations", min_len=2, max_len=MAX_N)
    e = binary_vector(args.get("events"), "events", min_len=2, max_len=MAX_N)
    if len(t) != len(e):
        raise StatsInputError("durations and events must have the same length")
    if (t < 0).any():
        raise StatsInputError("durations must be >= 0")
    if not e.any():
        raise StatsInputError("no events: survival estimates and tests are undefined")
    return t, e


def _labels(args: Mapping[str, Any], n: int, required: bool) -> list[str] | None:
    g = args.get("groups")
    if g is None:
        if required:
            raise StatsInputError("logrank needs groups")
        return None
    if not isinstance(g, list) or len(g) != n:
        raise StatsInputError("groups must be a list with one label per subject")
    labels = [str(x) for x in g]
    if not 2 <= len(set(labels)) <= MAX_GROUPS and required:
        raise StatsInputError(f"logrank needs 2-{MAX_GROUPS} groups")
    return labels


def _km_one(ll: Any, t: Any, e: Any, times: list[float], conf: float, rep: Report, label: str) -> dict[str, Any]:
    kmf = ll.KaplanMeierFitter(alpha=1 - conf).fit(t, e, label=label)
    sf, ci = kmf.survival_function_[label], kmf.confidence_interval_
    lo_ci, hi_ci = ci.iloc[:, 0], ci.iloc[:, 1]
    median = _num(kmf.median_survival_time_)
    med_ci = ll.utils.median_survival_times(ci)
    out: dict[str, Any] = {
        "group": label,
        "n": len(t),
        "events": int(e.sum()),
        "median": median,
        "median_ci": [_num(med_ci.iloc[0, 0]), _num(med_ci.iloc[0, 1])],
        "at_times": [
            {
                "time": x,
                "survival": float(kmf.predict(x)),
                "ci": [float(lo_ci[lo_ci.index <= x].iloc[-1]), float(hi_ci[hi_ci.index <= x].iloc[-1])],
            }
            for x in times
        ],
    }
    if median is None:
        rep.warn(f"group {label}: survival never falls to 0.5; the median is not reached (null)")
    if len(sf) <= MAX_CURVE:
        out["curve"] = [[float(x), float(s)] for x, s in sf.items()]
    else:
        rep.warn(f"group {label}: curve has {len(sf)} steps (> {MAX_CURVE}); use `times` for estimates")
    return out


def km(ll: Any, t: Any, e: Any, args: Mapping[str, Any], conf: float, rep: Report) -> dict[str, Any]:
    times = [float(x) for x in args.get("times", [])]
    labels = _labels(args, len(t), required=False)
    if labels is None:
        return {"groups": [_km_one(ll, t, e, times, conf, rep, "all")]}
    out = []
    for g in sorted(set(labels)):
        m = np.array([x == g for x in labels])
        if not e[m].any():
            rep.warn(f"group {g}: no events; its curve stays at 1")
        out.append(_km_one(ll, t[m], e[m], times, conf, rep, g))
    return {"groups": out}


def logrank(ll: Any, t: Any, e: Any, args: Mapping[str, Any], rep: Report) -> dict[str, Any]:
    labels = _labels(args, len(t), required=True)
    r = ll.statistics.multivariate_logrank_test(t, labels, e)
    return {
        "statistic": float(r.test_statistic),
        "df": _dof(r),
        "p": float(r.p_value),
        "groups": sorted(set(labels or [])),
        "statistic_name": "log-rank chi-square",
    }


def cox(ll: Any, t: Any, e: Any, args: Mapping[str, Any], conf: float, rep: Report) -> dict[str, Any]:
    import pandas as pd

    raw = args.get("covariates")
    if not isinstance(raw, Mapping) or not 1 <= len(raw) <= MAX_COVARIATES:
        raise StatsInputError(f"cox needs covariates: {{name: values}} with 1-{MAX_COVARIATES} entries")
    cols = {str(k): finite_vector(v, f"covariates[{k}]", min_len=len(t), max_len=len(t)) for k, v in raw.items()}
    constant = [k for k, v in cols.items() if np.ptp(v) == 0]
    if constant:
        raise StatsInputError(f"constant covariates cannot be estimated: {constant}")
    df = pd.DataFrame({**cols, "_T": t, "_E": e})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            fit = ll.CoxPHFitter(alpha=1 - conf).fit(df, "_T", "_E")
            ph = ll.statistics.proportional_hazard_test(fit, df, time_transform="km")
        except (ll.exceptions.ConvergenceError, np.linalg.LinAlgError, ValueError) as exc:
            raise StatsInputError(f"Cox model did not converge (separation or collinearity?): {exc}") from exc
    s = fit.summary
    lo_col = next(c for c in s.columns if c.startswith("exp(coef) lower"))
    hi_col = next(c for c in s.columns if c.startswith("exp(coef) upper"))
    coefs = [
        {
            "name": k,
            "coef": float(s.loc[k, "coef"]),
            "se": float(s.loc[k, "se(coef)"]),
            "hazard_ratio": float(s.loc[k, "exp(coef)"]),
            "hazard_ratio_ci": [float(s.loc[k, lo_col]), float(s.loc[k, hi_col])],
            "z": float(s.loc[k, "z"]),
            "p": float(s.loc[k, "p"]),
        }
        for k in cols
    ]
    lr = fit.log_likelihood_ratio_test()
    events = int(e.sum())
    if events / len(cols) < 10:
        rep.warn(f"{events} events for {len(cols)} covariates (< 10 per variable): estimates may be unstable")
    checks: list[dict[str, Any]] = [
        {"name": k, "statistic": float(ph.summary.loc[k, "test_statistic"]), "p": float(ph.summary.loc[k, "p"])}
        for k in cols
    ]
    flagged = [c["name"] for c in checks if c["p"] < 0.05]
    if flagged:
        rep.warn(
            f"Schoenfeld test rejects proportional hazards at 0.05 for {flagged}: consider strata or "
            "time-varying effects; hazard ratios then average over time"
        )
    return {
        "coefficients": coefs,
        "ties": "efron",
        "events": events,
        "likelihood_ratio": {
            "statistic": float(lr.test_statistic),
            "df": _dof(lr),
            "p": float(lr.p_value),
        },
        "concordance": float(fit.concordance_index_),
        "ph_check": {"method": "Schoenfeld residuals vs KM-transformed time, per covariate", "tests": checks},
    }


def concordance(ll: Any, t: Any, e: Any, args: Mapping[str, Any], rep: Report) -> dict[str, Any]:
    risk = finite_vector(args.get("risk_scores"), "risk_scores", min_len=len(t), max_len=len(t))
    c = float(ll.utils.concordance_index(t, -risk, e))
    rep.warn("no CI for the C-index (no validated interval implemented); compare models on the same cases")
    return {
        "c_index": c,
        "direction": "higher risk score = earlier event",
        "pair_rule": "comparable if the earlier time is an event; an event and a censoring at the same time are "
        "comparable (the censored subject outlived); tied risk scores count 1/2",
    }


def run(args: Mapping[str, Any]) -> str:
    conf = confidence_of(args)
    analysis = args.get("analysis")
    if analysis not in ANALYSES:
        raise StatsInputError(f"analysis must be one of {', '.join(ANALYSES)}")
    t, e = _base(args)
    ll = lifelines()
    rep = Report(
        NAME,
        {
            "km": "Kaplan-Meier",
            "logrank": "log-rank test",
            "cox": "Cox proportional hazards",
            "concordance": "Harrell's C-index",
        }[analysis],
        REFS[analysis],
        len(t),
    )
    rep.assumptions += ["Right censoring is independent of the event process (non-informative censoring)."]
    if analysis == "km":
        result = km(ll, t, e, args, conf, rep)
    elif analysis == "logrank":
        result = logrank(ll, t, e, args, rep)
    elif analysis == "cox":
        result = cox(ll, t, e, args, conf, rep)
    else:
        result = concordance(ll, t, e, args, rep)
    return rep.emit(result, analysis=analysis, confidence=conf)


def _impl(args: dict[str, Any], ctx: ToolContext) -> str:
    return run(args)


BINDING = ToolBinding(spec=SPEC, impl=_impl)
