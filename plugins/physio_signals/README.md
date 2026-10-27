# researchos-plugin-physio_signals 0.1.0

PhysioNet ECG/EEG datasets for Research OS (core contracts `>=1.7,<2`) plus a CPU ECG baseline template.
Imports only `researchos.contracts` and `researchos.sdk`. Network: `physionet.org` only, through the host
`HttpContext` (capability `network:physionet.org`).

## What

| Entry point | Group | What it builds |
|---|---|---|
| `physio_mitbih` | `researchos.datasources` | MIT-BIH Arrhythmia (mitdb 1.0.0): MLII beat windows (-0.25 s/+0.45 s around each annotated beat), `rr_pre_s`/`rr_post_s`, AAMI classes N/S/V/F/Q |
| `physio_ptbxl` | `researchos.datasources` | PTB-XL 1.0.3: one 10 s 12-lead record per sample (100 Hz default), diagnostic superclass label |
| `physio_eegmmi` | `researchos.datasources` | EEG Motor Movement/Imagery (eegmmidb 1.0.0, ODC-By): left/right fist trials (imagery runs 4/8/12 or movement 3/7/11), windows from each T1/T2 onset, explicit subject list, seeded subject-level split; mixed-rate records skipped and listed |
| `physio_chbmit` | `researchos.datasources` | CHB-MIT scalp EEG 1.0.0, **subset build**: fixed windows, ictal (1) vs background (0) |
| `physio_ecg_baseline` | `researchos.templates` | RR + morphology features, or NeuroKit2 R-peak detection + HRV features on continuous segments; logistic regression or random forest (CPU, seeded) |

Every table has `X`, `y`, `group` (patient/subject = leakage unit), `sample_id` (stable, e.g. `mitdb/100/1234`).
`extra` records upstream version, license and its source, sampling rate, leads/channels, units, label map,
per-class counts, exclusions, the upstream checksum status and a host split spec `extra["split"]`
(`{"kind": "groups", "assign": {...}}`, usable directly as a plan split).

## Splits (patient-level only)
- MIT-BIH: inter-patient DS1/DS2 (de Chazal et al., 2004). DS2 is `test`. DS1 subjects are split with a
  seeded patient-level partition into `train`/`search_val`/`confirm_val` (`seed`, `val_fraction`).
  Paced records 102/104/107/217 are excluded. **Record 202 is excluded**: records 201 (DS1) and 202 (DS2) are
  from the same subject (MIT-BIH directory), so the textbook DS2 would leak a subject. DS2 here has 21 records,
  which you should keep in mind when comparing with published DS2 numbers.
- PTB-XL: official `strat_fold`: 1-8 `train`, 9 `search_val`, 10 `test`; `confirm_fold` (1-8) optionally moves
  one training fold to `confirm_val` (default: `confirm_val` is empty). A patient found in two folds aborts.
- CHB-MIT: no official split; seeded patient-level partition (`seed`, `val_fraction`, `test_fraction`).
  `chb21` is the same subject as `chb01` (CHB-MIT description) and shares its group.
Every build verifies that no group is assigned to two splits and every row has a split.

## Labels
- MIT-BIH AAMI map: N = N,L,R,e,j; S = A,a,J,S; V = V,E; F = F; Q = /,f,Q. Other annotations are not beats.
  First/last beat of a record (no RR) and windows crossing the edge or holding invalid samples are dropped and
  counted in `extra["dropped_beats"]`.
- MIT-BIH segments (`unit: "segment"`): non-overlapping `segment_s` MLII segments from the record start, label 1
  when any reference beat in the segment is S, V or F (`any_SVF_beat`), else 0. `r_peaks` holds every WFDB beat
  annotation in the segment (incl. B, n, r, ? outside the AAMI map) (segment-relative samples, -1 padded to 4 beats/s; denser is an error), plus
  `sampling_rate_hz` per row. The trailing partial segment and segments with invalid samples are dropped and
  counted in `extra["dropped_segments"]`.
- PTB-XL: superclasses from `scp_statements.csv` (`diagnostic == 1`) as in the PTB-XL reference code (no
  likelihood threshold). Only records with exactly one superclass are kept; others are counted in
  `extra["excluded_records"]`.
- CHB-MIT: label 1 when a window overlaps a seizure interval from `chbNN-summary.txt` (stated seizure counts are
  cross-checked). All ictal windows are kept; background windows are evenly sub-sampled per file.

## Integrity
- Each file is downloaded with HTTP Range chunks of 4 MB or less (below the host's 5 MB cap) and hashed. If the
  project's `SHA256SUMS.txt` exists, every file must match it: a mismatch aborts the build. If it does not exist,
  `expected_source_sha256=None` and the host reports the dataset as unverified.
  `expected/observed_source_sha256` = SHA-256 over sorted `<sha256>  <path>` lines of the upstream files used.
- WFDB signals are checked against header initial values and 16-bit checksums. Truncated `.dat`/`.atr`/EDF files
  are errors. Nothing is padded or guessed.
- The license is read from the project's `LICENSE.txt` (ODC-By-1.0, ODbL-1.0, CC-BY-4.0, CC0-1.0 recognised).
  If it is unrecognised, the build fails unless the owner sets `license_override`.

## Settings (`dataset.build` payload `settings`)
- `physio_mitbih`: `records` (subset of DS1+DS2), `seed` (0), `val_fraction` (0.15), `unit` (`beat`|`segment`),
  `segment_s` (300, 10-1800), `license_override`.
- `physio_ptbxl`: `sampling_rate` (100|500), `folds` (1-10), `max_records_per_fold` (none),
  `confirm_fold` (none), `license_override`.
- `physio_chbmit`: `subjects` (**required**, e.g. `["chb01","chb02"]`), `seizure_records_only` (true),
  `window_s` (4), `max_background_windows_per_record` (60), `channels` (18 bipolar channels), `seed`,
  `val_fraction` (0.15), `test_fraction` (0.2), `license_override`.
JSON Schemas are in `MANIFEST.settings_schema` and each provider's `spec.settings_schema`.

## Template `physio_ecg_baseline`
Reads `ctx.data("train")` and `ctx.data(ctx.eval_split())` only. Features: bin-averaged beat morphology (+ RR pre,
post, ratio) for `X[N,T]`, per-lead mean/std/min/max/ptp for `X[N,T,leads]`. Params: `model` (`logreg`|`rf`),
`C`, `n_estimators`, `max_depth`, `class_weight` (`balanced` default), `morph_points` (32). It logs macro-F1,
`sensitivity_c<k>`/`ppv_c<k>` where defined (undefined values are not logged as 0), and confusion matrix
cells; it writes `confusion_matrix.json`, saves predictions with dataset `sample_id`s, reports `macro_f1` as
`recomputed`, and polls CANCEL. It stays **unreviewed** (no REVIEWED.json shipped). It needs `scikit-learn` and
`neurokit2==0.2.13` (`pip install "researchos-plugin-physio_signals[template]"`).

`features: "neurokit_hrv"` (BIOMED P2; needs `physio_mitbih` with `unit: "segment"`; code in `hrv.py`): per
segment, NeuroKit2 `ecg_clean` + `ecg_peaks` (`ecg_clean_method` / `ecg_peaks_method`: `neurokit` default or
`pantompkins1985`, no artifact correction), one-to-one match against the reference R peaks within 150 ms, and HRV
from the detected peaks (time domain and Poincaré computed in `hrv.py` from integer sample intervals):
MeanNN, SDNN, RMSSD (ms), pNN50 (%), Poincaré SD1/SD2 (ms, >= 20 beats), LF/HF (ms^2, NeuroKit2,
Welch, normalize=False, peak span >= 270 s) and LF/HF ratio. Unavailable values are null with quality flags in
`hrv_<split>.json` (columns with units, methods, per-segment counts); the classifier uses train-only median
imputation + missingness indicators. Logged: `r_peak_tp/fn/fp`, `r_peak_sensitivity`, `r_peak_ppv` on the
evaluation split (CIs: `stats_diag_metrics` on those counts). RR intervals are detected, not certified NN
intervals. **Research use only; no diagnostic or medical-device claim.**

`features: "mne_bandpower"` (EEG windows X[N, channels, samples] with per-row `sampling_rate_hz`, e.g. physio_chbmit;
code in `eeg.py`, MNE 1.10.2): MNE zero-phase FIR band-pass (`l_freq` 1, `h_freq` 45 Hz; must stay below Nyquist),
Welch PSD, log10 band power per channel for every band the window resolves (delta 1-4, theta 4-8, alpha 8-13,
beta 13-30, gamma 30-45 Hz). Artifact checks (peak-to-peak > `artifact_ptp`, default 500 in the data's units;
flat channels) are reported in `eeg_<split>.json` and logged as `artifact_windows`; `exclude_artifacts_train`
drops them from training only. Every run writes `model_card.json` (basis, verification, validation, applicability
limits) and `per_subject.json` (balanced accuracy per subject on the evaluation split).

## Limits
- Not run against live PhysioNet in this delivery (offline tests only). Real data volumes: MIT-BIH about 100 MB
  download; PTB-XL 100 Hz about 0.5 GB and about 21k requests (use `folds`/`max_records_per_fold`); CHB-MIT
  EDFs are about 40 MB each.
- CHB-MIT is a subset/window build, not a full continuous-EEG pipeline. Channel montage changes between files
  are handled by skipping files that lack a requested channel (`extra["skipped_records"]`).
- Only WFDB formats 16 and 212, single-segment records. EDF must have one shared sampling rate.
- DatasetDescriptor.license is a required string, so an unknown license is an error rather than None. See
  PROPOSED_CONTRACT_CHANGES.md.

## Tests
`pytest` (offline; SYNTHETIC fixtures generated by `tests/synth.py`, frozen sample in `fixtures/`). `test_hrv.py` also
checks R-peak detection on local MIT-BIH records 100/101/103 when `MITBIH_DATA_DIR` (unset: skipped)
holds them; it skips otherwise and never downloads.
