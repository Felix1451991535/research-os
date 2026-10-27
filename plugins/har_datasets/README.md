# Research OS har_datasets 0.1.0

UCI HAR supplied windows and WISDM v1 accelerometer windows, plus CPU RandomForest
and trainable 1D-CNN experiment templates.

## Registered objects

| Group | Entry | Purpose |
| --- | --- | --- |
| datasources | har_uci | UCI HAR train/test inertial signal text archives |
| datasources | har_wisdm | WISDM v1.1 raw text ZIP or tar archives |
| templates | har_np | Self-contained template; `model=rf`: training-only scaling, summary features, RandomForest; `model=cnn`: convolution/ReLU/global-average/dense classifier |

Both templates use RunContext, seed model randomness, read only train and
`eval_split()`, reject subject overlap and nonfinite inputs, and emit predictions,
accuracy, macro-F1 and `confusion_matrix.json` with a stable declared class order.
Macro-F1 averages the full configured class order, assigning zero to absent classes.
The matrix is an artifact, not a scalar metric. Device is explicitly recorded as
CPU. The CNN trains both convolution and dense parameters using NumPy backpropagation.
Single-class inputs are valid. No claim of competitive benchmark performance is made.

## Required dataset settings

`source_url`: HTTPS archive on archive.ics.uci.edu (UCI) or www.cis.fordham.edu (WISDM).
`source_version`: explicit source release identity.
`license`, `license_source`: caller-verified upstream license and its provenance.
`source_sha256`: trusted lower-case SHA-256 or null. A mismatch aborts before output.
Null means **upstream checksum unverified**, as permitted by PluginKit v2; an observed
download hash is never presented as an independently verified upstream checksum.
No source dataset is redistributed and no real checksum is invented.

Optional settings: `max_source_bytes` and `max_expanded_bytes` (at most 256 MiB),
`window_samples` (WISDM, default 128), `max_gap_ns` (default 150000000),
`split_seed` (default 0). Host HTTP response caps may be smaller and must be configured
by the owner to permit the approved source. The plugin cannot override HTTP policy.

UCI input must have 128 samples, nine named inertial channels, 1-based labels 1–6
and positive subject IDs. It preserves source partition and row in sample identities,
then provides a subject-wise split proposal across the combined source partitions.
The supplied UCI windows overlap; that fact is declared. WISDM uses disjoint windows
within contiguous subject/activity/monotonic timestamp segments. Incomplete tails
are dropped; malformed rows and unknown labels error rather than being filled.
Channels, documented format units and nominal sample rates are explicit descriptor
metadata. They are format assumptions, not measurements of the supplied archive.

Output: `data.npz` (X[N,T,C], y[N], group[N], sample_id[N]) plus `splits.json`.
Split algorithm: sorted unique subjects, seeded permutation, floor(20%) each for
test/validation with minimum one, remainder train; at least three subjects required.
The split file is an auditable proposal, not host split registration or test release.
The host must apply it under its own dataset/experiment workflow. Locked test access,
freeze approval and final evaluation remain exclusively host-controlled.

## Templates

`har_np` with `model="rf"`: `trees` 1–500 (default 50), `n_classes` 1–100 (default 6), `device="cpu"`.
`har_np` with `model="cnn"`: `epochs` 1–200 (default 20), `learning_rate` >0..1 (default .05),
`n_classes` and `device` as above. RandomForest cancellation is checked before and
after its bounded fit; CNN polls between epochs. These are small CPU baselines;
full-batch CNN memory grows with data size. CUDA and PAMAP2 are not included.
Templates must be hash-reviewed by the owner before running; no REVIEWED.json is shipped.

## Storage and trust boundaries

All writes stay in DatasetBuildContext.out_dir, the host's empty staging directory.
ZIP/tar paths, symlinks, duplicate members and expansion limits are checked; archive
members are read in memory, never extracted. Conversion writes a temporary NPZ and
renames it; whole-dataset commit, cleanup, caching and registration are host duties.
Cancellation and failures must cause the host to discard the staging directory.
Caller-selected arbitrary local paths, private downloads and automatic trusted-source
updates are not supported.

## Installation and validation

Install into a PluginKit v2 / Research OS contracts 1.2 environment:

```sh
pip install dist/*.whl
ruff check src tests
ruff format --check src tests
mypy --strict src tests
pytest -q
```

Development: `pip install -e ".[dev]"`. The wheel does not include or modify core.
The distribution version is 0.1.0; the manifest requires `>=1.2,<2` contracts.
All test fixtures are original synthetic CC0-1.0 data, never real research evidence.
Tests block socket connections; provider calls use SDK FakeHttp exclusively.
No tools, routes, migrations, approval handlers or private core imports are registered.
The host owns installation approval, operation authorization and final publication.

Mypy follows the public core's unmarked Python sources with `follow_untyped_imports`
because this snapshot lacks a `py.typed` marker. `follow_imports="silent"` retains
imported type information while reporting errors only in this plugin. The standard
parser (`native_parser=false`) avoids a mypy 2.4 CPU-affinity crash in this execution
environment; no strict checks are disabled. Small typed adapters describe the
untyped third-party APIs used. Core files remain unchanged.

`MANIFEST.sha256` hashes every packaged file except itself (a file cannot include
its own ordinary SHA-256 digest). Check from the plugin root with
`sha256sum -c MANIFEST.sha256`. Wheels contain runtime sources, type markers and
any template resources; tests and fixtures remain in the source archive.
