# Proposed contract changes (contracts 1.2 -> 1.3); core was NOT patched

## 1. `DatasetDescriptor.license: str | None`
The spec says "license (upstream else None)", but contracts 1.2 require a non-empty string and
DatasetProviderConformance asserts it is truthy. Current behaviour: an unrecognised or missing upstream license
makes the build fail unless the owner sets `license_override`. Nothing is guessed.
Proposal: allow `None`, have the host show "license unknown" and block redistribution/export of such datasets.
Acceptance: conformance accepts `None` and asserts the host never publishes files of a None-licensed dataset.

## 2. First-class split assignment in `DatasetDescriptor`
Patient-level split assignments (official DS1/DS2, `strat_fold`) are stored in `extra["split"]`
(`{"kind": "groups", "assign": {...}}`, the same shape as the host's run splits spec), so the host cannot
discover them without convention. Proposal: `DatasetDescriptor.splits: Mapping[str, tuple[str, ...]] | None`
(split -> group ids) plus a host check that the groups are disjoint. Acceptance: conformance verifies
disjointness and that every row's group appears in exactly one split.

## 3. Conformance helper keeps an `NpzFile` open (Windows)
`DatasetProviderConformance.test_build_is_verifiable_and_confined` calls `np.load(...)` without closing it. On
Windows the TemporaryDirectory cleanup then fails with WinError 32 after all assertions have passed (the core
`plugin_hello` example shows the same: 18 passed, 1 error on Windows). The plugin works around this in
`tests/conftest.py` (Windows-only `ignore_cleanup_errors=True`; no assertion changed).
Proposal: `with np.load(path, allow_pickle=False) as z:` in the suite.

## 4. Large upstream files vs. the 5 MB HttpContext cap
Files are fetched with HTTP `Range` chunks of 4 MB or less. Proposal: document that HostHttp forwards `Range`
and returns 206 responses (the current snapshot does, but it is not part of the contract), or add a streaming
`get_to_file(url, max_bytes)` for dataset builds. Acceptance: a conformance test with a > 5 MB file served in
ranges.

## 5. Dataset version pinning in `DataSourceSpec`
The PhysioNet version (e.g. `mitdb/1.0.0`) is part of the provider version string and `extra`. Proposal: an
explicit `upstream_version` field so reproducibility reports do not parse `extra`.
