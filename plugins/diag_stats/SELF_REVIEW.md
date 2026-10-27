# Self review: diag_stats 0.1.0

Status: all gates green (ruff, mypy --strict, pytest; no skip/xfail). This is a self review by the building
agent; **no independent statistical review** was done.

## Scope vs. BATCH2_SPEC
| Requirement | Where | Evidence |
|---|---|---|
| Read-only tools (`read_only=True`, `destructive=False`, no network) | `_common.tool_spec` | ToolConformance per tool; `test_entry_points_are_registered_and_load`; `test_tools_have_no_network_or_file_io`; `test_tools_write_no_files` |
| `stats_diag_metrics`: sens/spec/PPV/NPV/acc (Wilson + Clopper-Pearson), F1, balanced acc, LR+/LR- | `diag_metrics.py`, `proportions.py` | golden 2x2 x4 cases + 3x3 one-vs-rest vs. statsmodels `proportion_confint` |
| `stats_roc_auc`: DeLong CI + paired test | `roc.py` | golden: AUC (scipy mannwhitneyu), DeLong var/cov/z/p (independent O(m*n) kernel), tied scores |
| `stats_paired_tests`: McNemar exact/mid-p, Cohen's/Fleiss' kappa with CI | `paired.py` | golden vs. statsmodels `mcnemar(exact=True)`, `cohens_kappa`, `fleiss_kappa`; mid-p via scipy binom |
| `stats_cluster_bootstrap`: fixed seed, reports seed + resamples | `bootstrap.py`, `resample.py` | bit-for-bit test for all 8 metrics; naive independent re-implementation matches |
| `stats_multiplicity`: Holm, BH | `multiplicity.py` | golden vs. statsmodels `multipletests` (holm, fdr_bh), adjusted + reject |
| Strict validation; undefined -> None + warning, never 0 | `_common.py` | 21 parametrised validation cases; perfect classifier, all one class, zero cells, tiny n, ties |
| Every result: method, formula reference, n, assumptions/warnings | `_common.Report.emit` | asserted for every tool call in `tests/helpers.call` |
| Exporter `stats_report`: Markdown + CSV, MetricSpan/CiteSpan via ExportContext, final + unresolved -> error, only out_dir | `report.py`, `render.py` | ExporterConformance + `test_report.py` (final writes nothing, draft warnings, CSV injection, option/path confinement) |
| Golden fixtures from scipy/statsmodels, script included, rel err <= 1e-9 | `scripts/make_golden.py`, `fixtures/golden.json` | `test_golden.py` (`math.isclose(rel_tol=1e-9, abs_tol=1e-12)`) |

## Conformance suites (researchos.sdk), all subclassed in tests/
| Test class | researchos.sdk suite | Result |
|---|---|---|
| `TestClusterBootstrapTool` | `ToolConformance` | 2 passed |
| `TestDiagMetricsConfusionTool` | `ToolConformance` | 2 passed |
| `TestDiagMetricsTool` | `ToolConformance` | 2 passed |
| `TestMultiplicityTool` | `ToolConformance` | 2 passed |
| `TestPairedTestsTool` | `ToolConformance` | 2 passed |
| `TestRocAucTool` | `ToolConformance` | 2 passed |
| `TestStatsReportExporter` | `ExporterConformance` | 3 passed |
`stats_diag_metrics` has two ToolConformance subclasses (2x2 and the largest allowed 20x20 confusion matrix, to
prove that output stays within `max_output_bytes`).

## Environment
Windows (win32 10.0.26300); Python 3.11.15 in the core_snapshot `.venv`. numpy 2.4.6, scipy 1.17.1,
statsmodels 0.15.0 (golden generation only), ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, jsonschema 4.26. Gates run
against the installed wheel. The core is unmodified. Spot timings on this machine: cluster bootstrap AUC with
n = 20000 and 2000 resamples took 2.6 s; paired DeLong with n = 20000 took 0.03 s.

## Known gaps (see PROPOSED_CONTRACT_CHANGES.md)
- Invalid input raises `StatsInputError` (ValueError); there is no structured tool-error contract.
- `MetricValue` has no CI/n fields, so the exporter can only render CIs that were stored as separate metrics.
- DeLong and the log-method LR CI have no scipy/statsmodels reference. Their golden values come from an
  independent direct-definition implementation in the generation script, not from a library.

## Risks
- Wald-type CIs (DeLong AUC, Cohen's kappa) can be poor at small n or near the bounds. They are clipped with a
  warning, and zero variance gives null. Wilson/Clopper-Pearson are used for proportions.
- No continuity or Haldane corrections: LR CIs with a zero cell are null (by design, documented).
- Percentile cluster bootstrap with very few clusters is unreliable; the tool does not refuse G >= 2.
- Bit-for-bit reproducibility holds for the same numpy version. numpy may change Generator streams across
  major versions.

## Recorded gate output
```text
$ python -m build --wheel --no-isolation
Successfully built researchos_plugin_diag_stats-0.1.0-py3-none-any.whl
WARNING Both NO_COLOR and FORCE_COLOR environment variables are set, disabling color
* Getting build dependencies for wheel...
* Building wheel...
(exit 0)
```

```text
$ pip install --force-reinstall --no-deps dist/researchos_plugin_diag_stats-0.1.0-py3-none-any.whl
Successfully installed researchos-plugin-diag-stats-0.1.0
(exit 0)
```

```text
$ ruff check src tests scripts
All checks passed!
(exit 0)
```

```text
$ mypy --strict src tests scripts
Success: no issues found in 18 source files
(exit 0)
```

```text
$ pytest -v (per file)
tests/test_boundaries.py PASSED: 5
tests/test_conformance.py PASSED: 15
tests/test_edge_cases.py PASSED: 38
tests/test_golden.py PASSED: 15
tests/test_report.py PASSED: 8
============================= 81 passed in 2.22s ==============================
(exit 0)
```
