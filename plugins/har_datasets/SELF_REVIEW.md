# Self review: har_datasets 0.1.0

## Result

Required gates passed; 32 offline tests, including 8 SDK
conformance test methods. No skip/xfail. Tests were repeated against the built wheel
installed in an isolated target; imports were verified to resolve to wheel contents.
Reference setup used the supplied core and plugin_hello: 19 tests passed.
318 original core_snapshot files byte-identical to input ZIP.

## Conformance objects

- TestUci: DatasetProviderConformance (2)
- TestWisdm: DatasetProviderConformance (2)
- TestRF: TemplateConformance (2)
- TestCNN: TemplateConformance (2)

The manifest has one researchos.plugins entry; every functional object is separately
registered in the matching group. FileImporter has no SDK suite in contracts 1.2;
its parser behavior is tested directly where applicable. No tool binding is needed.

## Recorded gate output

```text
$ ruff check src tests
All checks passed!
Exit code: 0

$ ruff format --check src tests
11 files already formatted
Exit code: 0

$ mypy --strict src tests
Success: no issues found in 11 source files
Exit code: 0

$ pytest -q
................................                                         [100%]
32 passed in 17.90s
Exit code: 0

$ python -m build --wheel --no-isolation
Successfully built researchos_plugin_har_datasets-0.1.0-py3-none-any.whl
* Getting build dependencies for wheel...
* Building wheel...
Exit code: 0
```

## Installed-wheel validation

```text
All three imports resolve to installed wheel contents
Wheel-installed validation (PYTHONPATH points only to wheel_validation):
................................                                         [100%]
32 passed in 19.26s
Exit code: 0
```

## Test environment

```text
Python 3.12.14
ruff 0.17.0; mypy 2.4.0; pytest 9.1.1; numpy 2.5.4; scikit-learn 1.9.1; python-docx 1.2.0; bibtexparser 1.4.4; hatchling 1.32.4
```

## Scope, risks and gaps

CPU-only models, full-batch CNN memory, caller-verified source metadata; host must apply split proposal and collect artifacts.

Streaming/transactional storage, typed split registration, resolved device/artifact fencing.
See PROPOSED_CONTRACT_CHANGES.md for concrete additive proposals and release limits.
No live/paid API calls, real research datasets, credentials or benchmark results are
included. Synthetic fixtures cannot be interpreted as external scientific evidence.

Mypy uses its standard parser to avoid the environment's psutil CPU-affinity crash;
strict checking stays enabled. The public core is followed for type information
without modifying it. AST tests enforce the public import boundary, entry-point tests
load installed objects, ruff enforces C901 <=10, and source-size tests enforce <=400 lines.

Packaging audit hashes every file except MANIFEST.sha256 itself, verifies every
archive member, excludes environments/caches, and enforces the 5 MB member limit.
