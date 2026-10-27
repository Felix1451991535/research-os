"""Research planning: analytical power, sample size and minimum detectable effects."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from fractions import Fraction
from typing import Any

import scipy
import statsmodels
from researchos.contracts.gateway import ToolBinding, ToolContext
from scipy.optimize import brentq
from scipy.stats import norm
from statsmodels.stats.power import FTestAnovaPower, NormalIndPower, TTestIndPower, TTestPower

from ._common import Report, StatsInputError, as_int, tool_spec

NAME = "stats_power"
MAX_N = 10_000_000
# scipy noncentral t/F tails stay within ~1e-7 relative error down to here (mpmath-checked); 1e-20 fails.
MIN_ALPHA = 1e-10
DESIGNS = ("two_sample_t", "paired_t", "one_way_anova", "two_proportions", "diag_accuracy", "auc")
PROB = {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}
PROPERTIES: dict[str, Any] = {
    "design": {"enum": [*DESIGNS, "simulation", "correlation"]},
    "solve_for": {"enum": ["n", "power", "effect"]},
    "effect_source": {"type": "string", "minLength": 1, "maxLength": 1000},
    "effect_basis": {"enum": ["scientific", "cohen_convention"], "default": "scientific"},
    "alpha": {"type": "number", "minimum": MIN_ALPHA, "exclusiveMaximum": 1, "default": 0.05},
    "power": PROB,
    "effect": {"type": "number", "minimum": 0},
    "n": {"type": "integer", "minimum": 2, "maximum": MAX_N},
    "ratio": {"type": "number", "minimum": 0.01, "maximum": 100, "default": 1},
    "groups": {"type": "integer", "minimum": 2, "maximum": 1000},
    "alternative": {"enum": ["two-sided", "larger", "smaller"], "default": "two-sided"},
    "prevalence": PROB,
    "sensitivity": PROB,
    "specificity": PROB,
    "half_width": PROB,
    "null_auc": {**PROB, "default": 0.5},
}
SPEC = tool_spec(
    NAME,
    "Research planning only. Solve n, power or effect for t tests, balanced ANOVA, Cohen h proportions, or "
    "single AUC normal test. Buderer diagnostic precision supports n only. Supply the other two quantities; "
    "omit the solved quantity. n is group 1 (AUC: diseased), pairs, or ANOVA total. ratio = group2/group1 "
    "(AUC: nondiseased/diseased). effect is nonnegative d, dz, f, h or AUC; smaller uses a negative signed "
    "effect internally. State effect_source; use effect_basis=cohen_convention for conventional benchmarks. "
    "Simulation and correlation are unsupported in this release.",
    PROPERTIES,
    ("design", "solve_for", "effect_source"),
)
COHEN = "Cohen J (1988), Statistical Power Analysis for the Behavioral Sciences, 2nd ed.; statsmodels.stats.power"
BUDERER = "Buderer NMF (1996), Acad Emerg Med 3:895-900; doi:10.1111/j.1553-2712.1996.tb03538.x"
HANLEY = "Hanley JA, McNeil BJ (1982), Radiology 143:29-36, eq. 1-2; doi:10.1148/radiology.143.1.7063747"
COMMON = {"design", "solve_for", "effect_source", "effect_basis", "alpha"}
EXTRA = {
    "two_sample_t": {"ratio", "alternative"},
    "paired_t": {"alternative"},
    "one_way_anova": {"groups"},
    "two_proportions": {"ratio", "alternative"},
    "auc": {"ratio", "alternative", "null_auc"},
    "diag_accuracy": {"prevalence", "sensitivity", "specificity", "half_width"},
}


def _number(args: Mapping[str, Any], key: str, lo: float, hi: float, *, closed: bool = False) -> float:
    v = args.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise StatsInputError(f"{key} must be a finite number")
    if not (lo <= v <= hi if closed else lo < v < hi) or not math.isfinite(v):
        raise StatsInputError(f"{key} must be {'in' if closed else 'strictly between'} {lo} and {hi}")
    return float(v)


def _validate(args: Mapping[str, Any]) -> dict[str, Any]:
    a = dict(args)
    design, solve = a.get("design"), a.get("solve_for")
    if not isinstance(design, str) or design not in DESIGNS:
        raise StatsInputError(f"unsupported design: {design!r}; simulation and correlation are out of scope")
    if solve not in ("n", "power", "effect"):
        raise StatsInputError("solve_for must be n, power or effect")
    allowed = COMMON | EXTRA[design]
    if design != "diag_accuracy":
        allowed |= {"n", "power", "effect"} - {solve}
    if set(a) - allowed:
        raise StatsInputError(f"irrelevant or solved fields must be omitted: {sorted(set(a) - allowed)}")
    source = a.get("effect_source")
    if not isinstance(source, str) or not source.strip() or len(source) > 1000:
        raise StatsInputError(
            "effect_source must state the scientific justification or convention (1..1000 characters)"
        )
    a.setdefault("effect_basis", "scientific")
    if a["effect_basis"] not in ("scientific", "cohen_convention"):
        raise StatsInputError("effect_basis must be scientific or cohen_convention")
    a.setdefault("alpha", 0.05)
    _number(a, "alpha", 0, 1)
    if a["alpha"] < MIN_ALPHA:
        raise StatsInputError(f"alpha below {MIN_ALPHA} is outside the numerically validated range")
    return a


def _root(fn: Callable[[float], float], lo: float, hi: float) -> float:
    """Bounded monotone inversion; never accept a solver's unconverged fallback."""
    if fn(lo) >= 0:
        return lo
    bound = min(hi, max(lo * 2, 0.125))
    while fn(bound) < 0 and bound < hi:
        bound = min(hi, bound * 2)
    if not math.isfinite(fn(bound)) or fn(bound) < 0:
        raise StatsInputError("target is infeasible or not numerically estimable within supported bounds")
    try:
        value = float(brentq(fn, lo, bound, xtol=1e-10))
    except (ValueError, RuntimeError, OverflowError) as exc:
        raise StatsInputError("power solver could not converge for these inputs") from exc
    return value


def _allocation(a: Mapping[str, Any], n: float) -> tuple[int, int | None, int]:
    design = a["design"]
    if design == "one_way_anova":
        per = max(2, math.ceil(n / a["groups"]))
        if per * a["groups"] > MAX_N:
            raise StatsInputError(f"rounded total sample size exceeds {MAX_N}")
        return per * a["groups"], per, per * a["groups"]
    n1 = max(2, math.ceil(n))
    if design == "paired_t":
        return n1, None, n1
    # Decimal ratio: binary 100 * 1.1 is 110.00000000000001 and must not round up to 111.
    n2 = max(2, math.ceil(n1 * Fraction(str(a["ratio"]))))
    if n1 + n2 > MAX_N:
        raise StatsInputError(f"total sample size exceeds {MAX_N}")
    return n1, n2, n1 + n2


def _realized_ratio(a: Mapping[str, Any], n: int, n2: int | None) -> float:
    return n2 / n if "ratio" in a and n2 is not None else 1.0


def _test_inputs(a: dict[str, Any]) -> None:
    design, solve = a["design"], a["solve_for"]
    if design == "one_way_anova":
        a["groups"] = as_int(a.get("groups"), "groups", minimum=2, maximum=1000)
    else:
        a.setdefault("alternative", "two-sided")
        if a["alternative"] not in ("two-sided", "larger", "smaller"):
            raise StatsInputError("alternative must be two-sided, larger or smaller")
    if "ratio" in EXTRA[design]:
        a.setdefault("ratio", 1.0)
        _number(a, "ratio", 0.01, 100, closed=True)
    if solve != "n":
        a["n"] = as_int(a.get("n"), "n", minimum=2, maximum=MAX_N)
        if design == "one_way_anova" and (a["n"] % a["groups"] or a["n"] < 2 * a["groups"]):
            raise StatsInputError("balanced ANOVA n must be a multiple of groups, with at least two per group")
    if solve != "power":
        _number(a, "power", a["alpha"], 1)
    if solve != "effect":
        _number(a, "effect", 0, math.pi if design == "two_proportions" else 100, closed=True)
        if solve == "n" and a["effect"] == 0:
            raise StatsInputError("zero effect cannot achieve target power above alpha")


def _sm_power(a: Mapping[str, Any], n: float, effect: float, ratio: float) -> float:
    if effect == 0:
        return float(a["alpha"])
    kw = {"effect_size": effect, "alpha": a["alpha"]}
    design = a["design"]
    if design == "one_way_anova":
        return float(FTestAnovaPower().power(nobs=n, k_groups=a["groups"], **kw))
    kw["alternative"] = a["alternative"]
    if a["alternative"] == "smaller":
        kw["effect_size"] = -effect
    if design == "paired_t":
        return float(TTestPower().power(nobs=n, **kw))
    model = TTestIndPower() if design == "two_sample_t" else NormalIndPower()
    return float(model.power(nobs1=n, ratio=ratio, **kw))


def hanley_variance(auc: float, diseased: float, nondiseased: float) -> float:
    # Factor Q1-A^2 and Q2-A^2 to avoid cancellation near perfect discrimination.
    return (
        auc
        * (1 - auc)
        * (1 + (diseased - 1) * (1 - auc) / (2 - auc) + (nondiseased - 1) * auc / (1 + auc))
        / (diseased * nondiseased)
    )


def _auc_power(a: Mapping[str, Any], n: float, effect: float, ratio: float) -> float:
    null = a["null_auc"]
    se0 = math.sqrt(hanley_variance(null, n, n * ratio))
    se1 = math.sqrt(hanley_variance(effect, n, n * ratio))
    two = a["alternative"] == "two-sided"
    z = float(norm.isf(a["alpha"] / (2 if two else 1)))
    # The AUC estimate lies in [0, 1]: a rejection threshold outside it can never be reached.
    upper, lower = null + z * se0, null - z * se0
    p = float(norm.sf((upper - effect) / se1)) if upper < 1 else 0.0
    if two and lower > 0:
        p += float(norm.cdf((lower - effect) / se1))
    return p


def _auc_inputs(a: dict[str, Any]) -> None:
    a.setdefault("null_auc", 0.5)
    _number(a, "null_auc", 0, 1)
    if a["alternative"] == "smaller":
        raise StatsInputError("AUC supports two-sided or larger alternatives, with AUC above null_auc")
    if a["solve_for"] != "effect":
        _number(a, "effect", a["null_auc"], 1)


PowerFn = Callable[[Mapping[str, Any], float, float, float], float]


def _solve_n(
    a: Mapping[str, Any], calc: PowerFn, effect: float, ratio: float, minimum: float
) -> tuple[float, tuple[int, int | None, int]]:
    upper = MAX_N / (1 + ratio) if "ratio" in a else MAX_N
    raw_n = _root(lambda n: calc(a, n, effect, ratio) - a["power"], minimum, upper)
    n, n2, total = _allocation(a, raw_n)
    # AUC power jumps where the rejection threshold enters [0, 1], so the root can sit on the
    # unreachable side; step up until the integer allocation itself meets the target (1e-9: solver noise).
    for _ in range(3):
        if calc(a, n, effect, _realized_ratio(a, n, n2)) >= a["power"] - 1e-9:
            return raw_n, (n, n2, total)
        n, n2, total = _allocation(a, n + 1)
    raise StatsInputError("rounded allocation could not reach the target power")


def _test_result(a: dict[str, Any], rep: Report) -> dict[str, Any]:
    _test_inputs(a)
    is_auc = a["design"] == "auc"
    if is_auc:
        _auc_inputs(a)
    calc = _auc_power if is_auc else _sm_power
    ratio = a.get("ratio", 1.0)
    solve = a["solve_for"]
    minimum = 2 * a["groups"] if a["design"] == "one_way_anova" else max(2, 2 / ratio)
    raw_n = float(a.get("n", minimum))
    effect = float(a.get("effect", 0))
    if solve == "n":
        raw_n, (n, n2, total) = _solve_n(a, calc, effect, ratio, minimum)
    else:
        n, n2, total = _allocation(a, raw_n)
    realized_ratio = _realized_ratio(a, n, n2)
    if solve == "effect":
        lower = a["null_auc"] if is_auc else 0.0
        upper_effect = 1 - 1e-10 if is_auc else (math.pi if a["design"] == "two_proportions" else 100)
        effect = _root(lambda e: calc(a, n, e, realized_ratio) - a["power"], lower, upper_effect)
    achieved = calc(a, n, effect, realized_ratio)
    if not math.isfinite(achieved) or not 0 <= achieved <= 1:
        raise StatsInputError("power is not numerically estimable for these inputs")
    rep.n = total
    results: dict[str, Any] = {
        "n": n,
        "n_unit": {"paired_t": "pairs", "one_way_anova": "total"}.get(a["design"], "group1"),
        "n_total": total,
        "effect": effect,
        "power": achieved,
        "effect_scale": {
            "two_sample_t": "Cohen d",
            "paired_t": "Cohen dz",
            "one_way_anova": "Cohen f",
            "two_proportions": "Cohen h (magnitude)",
            "auc": "AUC",
        }[a["design"]],
    }
    if a["design"] == "one_way_anova":
        results["n_per_group"] = n2
    elif n2 is not None:
        results.update(n_group2=n2, realized_ratio=realized_ratio)
    if solve == "n":
        results.update(n_continuous=raw_n, target_power=a["power"])
    if is_auc:
        results["variance"] = hanley_variance(effect, n, n2 or n)
    return results


def _diagnostic(a: dict[str, Any], rep: Report) -> dict[str, Any]:
    if a["solve_for"] != "n":
        raise StatsInputError("diag_accuracy supports solve_for=n only: CI precision is not test power")
    prevalence = _number(a, "prevalence", 0, 1)
    width = _number(a, "half_width", 0, 1)
    if not {"sensitivity", "specificity"} & a.keys():
        raise StatsInputError("supply sensitivity and/or specificity")
    z2 = float(norm.isf(a["alpha"] / 2)) ** 2
    results: dict[str, Any] = {}
    required = []
    for key, fraction in (("sensitivity", prevalence), ("specificity", 1 - prevalence)):
        if key not in a:
            continue
        p = _number(a, key, 0, 1)
        raw = z2 * p * (1 - p) / width / width / fraction
        if not math.isfinite(raw) or raw > MAX_N:
            raise StatsInputError(f"required total sample size exceeds {MAX_N}")
        count = math.ceil(raw)
        required.append(count)
        results[key] = {"n_continuous": raw, "n_total": count, "expected_class_count": count * fraction}
    rep.n = max(required)
    results.update(n=rep.n, n_total=rep.n, n_unit="total", power=None)
    rep.warn("power is null: Buderer is a CI precision calculation, not a hypothesis test")
    rep.warn(
        "Wald precision is approximate, especially near 0/1; prevalence gives expected, not guaranteed class counts"
    )
    return results


ASSUMPTIONS = {
    "two_sample_t": "Independent observations; normal outcomes with equal population variances; d uses pooled SD.",
    "paired_t": "Independent complete pairs; normal within-pair differences; dz uses difference SD.",
    "one_way_anova": "Balanced independent groups; normal residuals and common variance; omnibus F test, Cohen f.",
    "two_proportions": "Independent binomial groups; normal approximation, h=2 asin(sqrt(p1))-2 asin(sqrt(p2)); "
    "no continuity correction. An h MDE alone does not determine feasible raw probabilities.",
    "diag_accuracy": "Independent subjects sampled at specified prevalence; valid reference standard; "
    "n=max(z^2 Se(1-Se)/(L^2 prevalence), z^2 Sp(1-Sp)/(L^2 (1-prevalence))). "
    "Marginal two-sided intervals, no simultaneous-coverage correction.",
    "auc": "Independent diseased and nondiseased subjects; continuous scores; Hanley-McNeil Q1=A/(2-A), "
    "Q2=2A^2/(1+A) approximation. Normal rejection threshold uses null variance; power uses alternative "
    "variance. AUC is above the null; no paired ROC comparison or prevalence-based recruitment.",
}


def run(args: Mapping[str, Any]) -> str:
    a = _validate(args)
    design = a["design"]
    ref = {"diag_accuracy": BUDERER, "auc": HANLEY}.get(design, COHEN)
    rep = Report(NAME, f"{design}: analytical {a['solve_for']}", ref, 0)
    rep.assumptions += [
        ASSUMPTIONS[design],
        f"Effect source ({a['effect_basis']}): {a['effect_source']}",
        "Research planning only; independent experimental units; no attrition, clustering or multiplicity adjustment.",
    ]
    if a["effect_basis"] == "cohen_convention" or "convention" in a["effect_source"].lower():
        rep.warn("Cohen convention only: a small/medium/large benchmark is not evidence of scientific importance")
    if a["solve_for"] == "power":
        rep.warn("Use prespecified effects; observed post-hoc power adds no evidence beyond the observed test")
    try:
        results = _diagnostic(a, rep) if design == "diag_accuracy" else _test_result(a, rep)
    except ArithmeticError as exc:
        raise StatsInputError("inputs exceed numerical precision; calculation is not estimable") from exc
    return rep.emit(results, inputs=a, versions={"statsmodels": statsmodels.__version__, "scipy": scipy.__version__})


def _impl(args: dict[str, Any], ctx: ToolContext) -> str:
    return run(args)


BINDING = ToolBinding(spec=SPEC, impl=_impl)
