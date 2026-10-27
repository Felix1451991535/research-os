# Changelog

## template physio_ecg_baseline 0.4.0 (2026-10-17)
- `physio_eegmmi` `local_dir`: build from an owner local copy via files:read (ADR-189).
- `physio_synth_eeg`: synthetic known-truth motor-imagery EEG provider (ADR-187).
- `features: "mne_csp"`: MNE common spatial patterns for motor-imagery EEG, filters fit on train only (ADR-186).

## 0.1.0+ros1 (merged into Research OS, 2026-10-12)
- No code change. `ruff format` applied; passes the new TemplateConformance self-containment and requires checks.

## 0.1.0 (2026-10-12)
- DatasetProviders `physio_mitbih` (MIT-BIH Arrhythmia 1.0.0, AAMI N/S/V/F/Q beats, inter-patient DS1/DS2),
  `physio_ptbxl` (PTB-XL 1.0.3, diagnostic superclasses, official `strat_fold`), `physio_chbmit` (CHB-MIT 1.0.0,
  seizure windows, seeded patient-level split). Host `physionet.org` only, through the host HttpContext.
- Patient-level splits only, with a disjointness check in every build (`LeakageError`). Same-subject records
  share a group: MIT-BIH 201/202 (202 excluded from DS2) and CHB-MIT chb01/chb21.
- Pure WFDB (formats 16/212, header/annotation parsing, checksum and length checks) and EDF readers. Corrupt or
  truncated files are errors.
- Upstream `SHA256SUMS.txt` verification (mismatch aborts; none -> `expected_source_sha256=None`); license from
  `LICENSE.txt` or an explicit `license_override`.
- TemplatePackage `physio_ecg_baseline`: deterministic CPU baseline (RR + morphology features, logistic
  regression / random forest). Logs macro-F1, per-class sensitivity/PPV and confusion cells; saves predictions;
  polls cancel; unreviewed.
