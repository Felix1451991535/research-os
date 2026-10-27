# Changelog

## Unreleased (relay review, 2026-10-14)
- McNemar with no discordant pairs returns p_exact = p_mid = 1.0 (exact definition, matches statsmodels) plus a
  warning, instead of null.

## 0.1.0+ros1 (merged into Research OS, 2026-10-12)
- `StatsInputError` is a core 1.3 `ToolError` (`invalid_input`); `core_contract >=1.3,<2`.
- mypy fix for numpy 2.5 stubs (`int(np.max(inverse))`); `ruff format` applied.

## 0.1.0 (2026-10-12)
- Read-only ToolBindings: `stats_diag_metrics` (2x2 / KxK one-vs-rest; Wilson + Clopper-Pearson CIs; F1,
  balanced accuracy, LR+/LR- with log-method CIs), `stats_roc_auc` (DeLong CI, paired DeLong test),
  `stats_paired_tests` (McNemar exact / mid-p, Cohen's kappa with asymptotic CI, Fleiss' kappa with bootstrap
  CI), `stats_cluster_bootstrap` (seeded subject-clustered percentile CI), `stats_multiplicity` (Holm, BH).
- Strict input validation; undefined values are null + warning; every result carries method, formula reference,
  n, assumptions and warnings.
- Exporter `stats_report`: statistics appendix Markdown + CSV, reporting checklist, number traceability table;
  unresolved spans are errors in final mode (no files written).
- Frozen reference values (`fixtures/golden.json`) from scipy/statsmodels via `scripts/make_golden.py`.
