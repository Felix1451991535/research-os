# Changelog

## 0.1.0+ros1 (merged into Research OS, 2026-10-12)
- Host review fix (security): templates `har_rf`/`har_cnn` imported the plugin package (`models.py`), which the
  owner-reviewed template hash does not cover. Replaced by one self-contained template `har_np` (`model=rf|cnn`)
  whose directory holds all executed code.
- Host review fix: `requires` used the distribution name `scikit-learn`; the host checks import names → `sklearn`
  (the template would always have been blocked as missing_dependency).
- WISDM: records split on `;` (the public file has concatenated records); malformed records are skipped and counted
  (`skipped_malformed_records`), more than 1% is an error. Behaviour on the real file: NOT VERIFIED (no download).
- UCI: `official_test_subjects` recorded in the descriptor so results stay comparable with the literature.

## 0.1.0
Replaced the v1 manifest-only scaffold with contracts 1.2 implementations and offline tests.
