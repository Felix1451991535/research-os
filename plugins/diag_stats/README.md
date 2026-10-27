# researchos-plugin-diag_stats 0.1.0

Diagnostic-accuracy statistics for Research OS agents (core contracts `>=1.8,<2`): eight read-only tools and a
statistics-appendix exporter. Imports only `researchos.contracts` / `researchos.sdk` (+ numpy, scipy, statsmodels). No network
(capabilities `[]`), no settings. Tools write no files. The exporter writes only inside `ExportContext.out_dir`.

## Tools (`researchos.tools`; all `read_only=True`, `destructive=False`, `side_effect_scope="none"`)
| Tool | Input | Output |
|---|---|---|
| `stats_diag_metrics` | `tp, fp, fn, tn` or `confusion` (KxK, rows = reference class) | sensitivity, specificity, PPV, NPV, accuracy with Wilson and Clopper-Pearson CIs; F1; balanced accuracy; LR+/LR- with log-method CIs; one-vs-rest per class + overall accuracy, macro sensitivity and macro F1 |
| `stats_roc_auc` | `y_true` (0/1), `scores`, optional `scores_b` | AUC (ties count 1/2), DeLong SE and normal CI; paired DeLong test (difference, CI, z, p, covariance) |
| `stats_paired_tests` | `test` = `mcnemar` / `cohen_kappa` / `fleiss_kappa`, `table` | McNemar exact binomial and mid-p; Cohen's kappa with asymptotic SE/CI (Fleiss-Cohen-Everitt 1969); Fleiss' kappa with seeded subject-bootstrap CI |
| `stats_cluster_bootstrap` | `metric`, `y_true`, `y_pred` or `scores`, `clusters`, `resamples`, `seed` | point estimate + percentile CI from resampling whole clusters (subjects); reports seed, resamples, defined and undefined resamples |
| `stats_power` | `design`, `solve_for`, `effect_source`, design-specific planning inputs | analytical sample size, power or minimum detectable effect; assumptions, allocation, provenance and warnings |
| `stats_multiplicity` | `p_values`, `methods` (`holm`, `bh`), `alpha` | adjusted p-values (input order), rejections, count |
| `stats_compare_groups` | `groups` [{name, values}] + `paired` (+ `decimals`), or `table`; `test` (`auto` = by design: Welch t / paired t / Welch ANOVA / Fisher or chi-square; others on request), `alpha` (diagnostic flags), `seed`, `confidence` | statistic, df, p with its method (exact / exhaustive permutation / approximation), effect size (CI only where coverage was checked: mean difference, Hedges g and d_z by noncentral t, rank-biserial by Newcombe, eta^2, Cramer V, odds ratio), diagnostics that never change the test, APA string (ADR-147, ADR-151) |
| `stats_survival` | `analysis` (`km`, `logrank`, `cox`, `concordance`), `durations`, `events`, `groups`, `covariates`, `risk_scores`, `times`, `confidence` | Kaplan-Meier with Greenwood log(-log) CI and medians, log-rank test, Cox PH (Efron ties) with hazard ratios, LR test, concordance and Schoenfeld PH check, Harrell C (needs extra `survival`: lifelines, MIT; ADR-153) |

Every output is one JSON object with `tool`, `method`, `formula_reference`, `n`, `assumptions`, `warnings` and
`results` (plus the confidence level and other inputs that define the result). Every metric also carries its
`formula`.
- **Undefined is never 0.** 0/0, an empty class, zero variance (e.g. perfect separation) or no discordant pairs
  give `null` plus a warning that names the quantity.
- **Strict validation.** Wrong shapes, unequal lengths, labels other than 0/1 (booleans rejected), NaN/inf,
  negative or non-integer counts, all-zero tables, non-square matrices, duplicate labels, unequal raters
  (Fleiss) and fewer than 2 clusters raise `StatsInputError` (a ValueError) with a precise message.
- Output is bounded (`max_output_bytes` 64000; the schemas cap input sizes). An oversized result raises an
  error rather than being truncated into invalid JSON.
- Bootstrap: resample *r* draws G cluster indices with `numpy.random.default_rng(seed).integers(0, G, size=G)`,
  one call per resample, so results are bit-for-bit identical for the same seed, input and numpy version.

## Exporter `stats_report` (`researchos.exporters`)
Writes `<basename>.md` (the statistics appendix) and `<basename>.csv` (one row per number and per citation, values
at full precision). Options: `basename` (default `stats_appendix`), `decimals` (4..10, default 4).
- Numbers only from `MetricSpan`, resolved through `ctx.metric` and rendered as `0.9123 [M1]`. The traceability
  table lists run, metric, split and verification status. Citations only from `CiteSpan` via `ctx.citation`,
  with a reference list built from the BibRecord.
- Checklist: numbers resolved, numbers recomputed or invariant-checked, split stated, citations resolved,
  figures resolved. It states that study-design items (STARD 2015 / TRIPOD) still need a human check.
- Diagnostics: `unresolved_citation`, `unresolved_metric`, `missing_artifact` and `nonfinite_metric` are errors in
  `final` mode (no files are written, the host marks the export failed) and warnings in `draft` mode, with
  visible `[unresolved:..]` / `[missing:..]` markers. `unverified_metric` (for example self_reported) is always a
  warning. `invalid_options` and `unsupported_kind` are errors.
- CSV text cells starting with `= + - @` are prefixed with `'` (spreadsheet formula injection).

## Reference values
`fixtures/golden.json` is produced by `scripts/make_golden.py` (`pip install -e ".[golden]"`), which never imports
the plugin. Proportion CIs, McNemar, Cohen's and Fleiss' kappa, Holm and BH come from statsmodels. AUC and the
p-values come from scipy. DeLong variance/covariance and the log-method LR intervals have no scipy/statsmodels
implementation, so they are computed from their published definitions with an independent O(m*n) kernel. The
tests require relative error <= 1e-9 (absolute 1e-12 only where the reference is exactly 0).

## Limits
- CIs are frequentist large-sample (DeLong, kappa) or exact/score (proportions). There are no continuity
  corrections, and the Haldane 0.5 correction is not applied to zero cells (those CIs are null instead).
- The Fleiss' kappa CI is a subject bootstrap (no closed-form non-null variance is used).
- The cluster bootstrap assumes independent clusters. The percentile interval can be poor for very few clusters.
- Not validated against a statistician's independent review; see SELF_REVIEW.md.

## Research planning (`stats_power`, ADR-135)

Supply `design`, `solve_for` (`n`, `power`, `effect`), and a nonblank `effect_source` describing the
prespecified scientific justification, prior estimate, convention, or sensitivity-analysis question.
Omit the quantity being solved; supply the other two. Defaults: `alpha=0.05`, `alternative="two-sided"`,
`ratio=1`. No default effect or target power. `effect_basis="cohen_convention"` explicitly marks a
Cohen benchmark and emits a warning (also detected if the source mentions convention).
`scientific` is caller-declared provenance, not verification. No post-hoc data inference is performed.

| Design | Effect | Input/result `n` | Additional inputs / method |
|---|---|---|---|
| `two_sample_t` | nonnegative Cohen d | first group | `ratio=n2/n1`; equal-variance `TTestIndPower` |
| `paired_t` | nonnegative Cohen dz (difference SD) | complete pairs | `TTestPower`; pairs independent |
| `one_way_anova` | Cohen f | total | required `groups`; balanced `FTestAnovaPower` |
| `two_proportions` | nonnegative Cohen h | first group | `ratio`; `NormalIndPower`, arcsine transformation |
| `auc` | anticipated AUC above null | diseased group | `ratio=nondiseased/diseased`, `null_auc=0.5`; normal test |
| `diag_accuracy` | `sensitivity` and/or `specificity` | total recruited | `prevalence`, `half_width`; Buderer; `solve_for=n` only |

For t/proportion tests `alternative="larger"` or `"smaller"` selects the direction; the latter applies
the negative of the supplied effect magnitude internally. ANOVA is an upper-tail omnibus F test and
rejects `alternative`. AUC supports `two-sided`/`larger`, with `null_auc < effect < 1`; a rejection
threshold outside [0, 1] is unreachable and contributes zero power. `alpha` must lie in [1e-10, 1):
below that, noncentral t/F tails lose accuracy. Group 2 is `ceil(n * ratio)` using the decimal ratio.
Cohen h is `2 asin(sqrt(p1)) - 2 asin(sqrt(p2))`; its magnitude cannot exceed pi.
An h result is not a raw probability difference; translating it requires a baseline probability.

For `n`, round upward: ANOVA to a multiple of groups (at least two per group), other designs to whole
units (at least two per group/pairs). Second groups use `max(2, ceil(n*ratio))`. Report achieved power
at the realized ratio, continuous n, target power, total n, and group counts. Top-level `n` is total
independent units (paired designs count pairs). No dropout, cluster or multiplicity inflation is included.
Inputs record defaults and library versions. Solvers use statsmodels power functions with bounded,
expanding brackets; no unconverged statsmodels fallback is accepted. Limits: total n <= 10 million,
ratio 0.01..100, groups 2..1000, standardized d/dz/f <= 100. Out-of-range or numerically infeasible
requests raise `StatsInputError`. Unknown, irrelevant, and solved-quantity inputs also raise errors.

Buderer uses `z²*p*(1-p)/(half_width²*class_fraction)`, taking the larger total when both endpoints are
supplied. Both endpoint estimates and prevalence must be strictly between zero and one. These are marginal
Wald intervals, not simultaneous confidence coverage. Power is `null` with a warning: CI precision does
not define a hypothesis test. Prevalence predicts expected class counts, not guaranteed counts.

AUC variance is `[A(1-A)+(m-1)(Q1-A²)+(k-1)(Q2-A²)]/(m*k)`, where m is diseased, k nondiseased,
`Q1=A/(2-A)` and `Q2=2A²/(1+A)`. This is the Hanley–McNeil negative-exponential approximation for continuous
scores. The normal critical region uses variance at `null_auc`; integrate a normal distribution centered
at the anticipated AUC using its alternative variance to obtain power. Effect solving returns the minimum
detectable AUC above the null. This test construction is explicitly an approximation built on the published
variance, not a claim that Hanley–McNeil provides this exact power test. It does not compare correlated ROCs.
Small samples, ties, or different score distributions can invalidate the approximation.

Example (conventional benchmark, not a scientific effect justification):
```json
{"design":"two_sample_t","solve_for":"n","effect":0.5,"power":0.8,
 "effect_source":"Cohen medium benchmark only","effect_basis":"cohen_convention"}
```
Returns 64 per group (128 total), achieved power about 0.80146, and a convention warning.
`simulation` and `correlation` return explicit unsupported-design errors; there is no simulation stub.
This is research-only planning, not clinical deployment. No hospital adapter or new host contract is introduced.
The result is self-contained for a future experiment-plan attachment; persistence/UI integration is deferred.

Validation sources:
- [Cohen (1988), table 2.4.1](https://not-equal.org/content/pdf/misc/Cohen1988.pdf): two-sided alpha .05,
  power .80, d .5/.8 give 64/26 per group. The table's d .2 approximation is 393; exact noncentral-t
  inversion gives 393.4057 and conservative rounding gives 394. Tests preserve this distinction.
- [Buderer (1996), p899 and Appendix A](https://onlinelibrary.wiley.com/doi/pdf/10.1111/j.1553-2712.1996.tb03538.x):
  sensitivity .90, specificity .85, prevalence .20, half-width .10 gives totals 173/62 and overall 173.
  The paper rounds z to 1.96; our normal quantile is unrounded, so continuous values differ slightly.
- [Hanley–McNeil (1982), p33, equations 1–2](https://www.med.mcgill.ca/epidemiology/hanley/software/hanley_mcneil_radiology_82.pdf):
  AUC .85 with 40/60 per class gives SE .0437/.0356 (rounded published values). Independent rational
  formula checks include unequal class sizes to detect Q1/Q2 reversal.
- [statsmodels power classes](https://www.statsmodels.org/stable/stats.html#power-and-sample-size-calculations):
  synthetic golden scenarios from `scripts/make_golden.py`, which never imports the plugin. Direct power
  values and solver values are frozen; solver comparisons allow 3e-6 relative error for statsmodels'
  default root tolerance, supplemented by tighter round-trip checks on achieved power.

Method guidance consulted: local `statistical-power` and `statsmodels` Scientific Agent Skills;
no skill code was vendored. Published references above are the numerical authorities.
