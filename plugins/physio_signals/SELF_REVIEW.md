# Self review: physio_signals 0.1.0

Status: all gates green offline (ruff, mypy --strict, pytest; no skip/xfail). This is a self review by the
building agent; **no independent review** was done. It was not run against live PhysioNet.

## Scope vs. BATCH2_SPEC
| Requirement | Where | Evidence |
|---|---|---|
| DatasetProvider x3, host physionet.org only | `mitbih.py`, `ptbxl.py`, `chbmit.py`, `fetch.py` | conformance x3, `test_only_physionet_is_contacted` |
| TemplatePackage `physio_ecg_baseline` | `templates/physio_ecg_baseline/` | TemplateConformance x2 (logreg/MIT-BIH, rf/PTB-XL) |
| Patient-level splits only; MIT-BIH DS1/DS2; PTB-XL strat_fold 1-8/9/10 | `common.py`, providers | `test_no_subject_in_more_than_one_split` (per provider), `test_mitbih_protocol_and_labels`, `test_ptbxl_folds_labels_and_exclusions` |
| Leakage test per provider | `common.check_disjoint` | parametrised leakage test; `test_ptbxl_patient_in_two_folds_aborts`; 201/202 and chb01/chb21 same subject |
| Upstream checksums or `expected_source_sha256=None`; mismatch fails | `fetch.Upstream` | `test_checksum_mismatch_fails_the_build`, `test_missing_checksum_list_is_reported_unverified` |
| WFDB parser on synthetic record incl. corrupt/truncated -> error | `wfdb.py`, `edf.py` | 13 tests in `test_parsers.py` |
| Unknown metadata -> None / error | parsers, `detect_license` | units None, license unknown -> build error unless `license_override` |
| Template: deterministic CPU seed, RR+morphology, LR/RF, only ctx.data/ctx.eval_split, macro-F1, per-class sens/PPV, confusion, save_predictions, cancel, unreviewed | `run.py` | `test_template.py` (determinism, cancel, metrics, no REVIEWED.json) |
| Imports only researchos.contracts/sdk | all | `test_only_public_core_modules_are_imported` |

## Conformance suites (researchos.sdk), all subclassed in tests/
| Test class | researchos.sdk suite | Result |
|---|---|---|
| `TestChbMit` | `DatasetProviderConformance` | 2 passed |
| `TestEcgBaselineMitBih` | `TemplateConformance` | 5 passed |
| `TestEcgBaselinePtbXlRandomForest` | `TemplateConformance` | 2 passed |
| `TestMitBih` | `DatasetProviderConformance` | 2 passed |
| `TestPtbXl` | `DatasetProviderConformance` | 2 passed |

## Environment
Windows (win32 10.0.26300); Python 3.11.15 in the core_snapshot `.venv` (core requires `<3.14`). numpy 2.4.6,
scikit-learn 1.9.1, ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, jsonschema 4.26, pydantic 2.14.1, build + hatchling.
Core installed with `pip install -e ".[dev,experiments]"`, unmodified. Gates run against the **installed wheel**.
The kit sanity check `pytest examples/plugin_hello/tests` gives 18 passed + 1 failed **on Windows**
(WinError 32: open NpzFile during TemporaryDirectory cleanup), not the 19 passed expected on Linux.
`tests/conftest.py` applies a Windows-only `ignore_cleanup_errors=True` shim; no assertion is skipped or
altered, and on Linux/macOS it does nothing.

## Known gaps (see PROPOSED_CONTRACT_CHANGES.md)
- `DatasetDescriptor.license` must be a string: an unknown license is a build error (or owner `license_override`),
  not None.
- Split assignment lives in `extra["split"]` (no contract field).
- MIT-BIH DS2 has 21 records (202 excluded, same subject as DS1's 201); published DS2 results use 22.
- PTB-XL `confirm_val` is empty unless `confirm_fold` is set. CHB-MIT has no official split (seeded partition).

## Risks
- Parsers are verified only against synthetic records written by the test encoder (`tests/synth.py`), so a
  shared misunderstanding of the WFDB/EDF format would not be caught. Validate on real PhysioNet files before
  relying on results.
- Downloads rely on HTTP Range (<= 4 MB chunks) under the host 5 MB cap. If a server ignores Range for a file
  larger than 5 MB, that build fails (fail-safe, not silent).
- Real volumes: PTB-XL at 100 Hz is about 21k requests; per-host rate limiting makes full builds slow.
- License detection is pattern-based on `LICENSE.txt`; an unrecognised text fails closed.

## Recorded gate output
```text
$ python -m build --wheel --no-isolation
Successfully built researchos_plugin_physio_signals-0.1.0-py3-none-any.whl
WARNING Both NO_COLOR and FORCE_COLOR environment variables are set, disabling color
* Getting build dependencies for wheel...
* Building wheel...
(exit 0)
```

```text
$ pip install --force-reinstall --no-deps dist/researchos_plugin_physio_signals-0.1.0-py3-none-any.whl
Successfully installed researchos-plugin-physio-signals-0.1.0
(exit 0)
```

```text
$ ruff check src tests
All checks passed!
(exit 0)
```

```text
$ mypy --strict src tests
Success: no issues found in 15 source files
(exit 0)
```

```text
$ pytest -v (per file)
tests/test_boundaries.py PASSED: 4
tests/test_parsers.py PASSED: 13
tests/test_providers.py PASSED: 25
tests/test_template.py PASSED: 8
============================= 50 passed in 18.65s =============================
(exit 0)
```
