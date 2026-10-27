"""Model card and per-subject results for every run of the physio baseline (ADR-177): what the model is based on,
how its parts are verified, how this run is validated, and where it does not apply. Template-local."""

from __future__ import annotations

from typing import Any

import numpy as np

Array = np.ndarray[Any, Any]

BASIS = {
    "morphology": "bin-averaged beat morphology + RR features (X[N,T]) or per-lead summary statistics (X[N,T,leads])",
    "neurokit_hrv": "NeuroKit2 ECG cleaning + R-peak detection, HRV from detected peaks (time, Poincare, Welch LF/HF)",
    "mne_bandpower": "MNE FIR band-pass + Welch PSD, log10 band power per channel (delta..gamma)",
    "mne_csp": "MNE FIR band-pass (default 8-30 Hz, mu/beta) + common spatial patterns (Ramoser et al. 2000; "
    "mne.decoding.CSP), log-variance features; filters fit on the training split only",
}
VERIFICATION = {
    "morphology": "template conformance tests on synthetic MIT-BIH / PTB-XL tables",
    "neurokit_hrv": "hand formulas (time domain, Poincare), sinusoidal HF in ms^2, synthetic ECG heart rate, "
    "R-peak agreement on local MIT-BIH records",
    "mne_bandpower": "synthetic rhythms land in the expected band (10 Hz -> alpha), artifact and flat-channel "
    "detection, band resolution checks",
    "mne_csp": "planted lateralised 10 Hz rhythm decoded; filters unchanged when evaluation data change "
    "(train-only fit); class/component/rate errors",
}
LIMITS = {
    "morphology": ["beat windows or fixed-length records only"],
    "neurokit_hrv": ["frequency domain needs >= 270 s of detected peaks", "detected RR, not certified NN intervals"],
    "mne_bandpower": [
        "a band is used only if the window resolves it (>= 2 Welch bins)",
        "artifact threshold is in the data's units (CHB-MIT: microvolts)",
        "one sampling rate per dataset",
    ],
    "mne_csp": [
        "supervised spatial filters: needs >= 2 classes in training and the same channel montage in every split",
        "sensitive to artifacts and to non-stationarity between subjects (cross-subject transfer is not guaranteed)",
        "one sampling rate per dataset",
    ],
}


def per_subject(y_true: Array, y_pred: Array, groups: Array) -> dict[str, dict[str, Any]]:
    """Balanced accuracy per subject (mean recall over the classes present for that subject)."""
    out: dict[str, dict[str, Any]] = {}
    for g in sorted(set(groups.tolist())):
        m = groups == g
        recalls = [float(np.mean(y_pred[m][y_true[m] == c] == c)) for c in sorted(set(y_true[m].tolist()))]
        out[str(g)] = {"n": int(m.sum()), "classes": len(recalls), "balanced_accuracy": float(np.mean(recalls))}
    return out


def model_card(mode: str, params: dict[str, Any], settings: dict[str, Any], split: str) -> dict[str, Any]:
    return {
        "template": "physio_ecg_baseline",
        "features": mode,
        "classifier": params.get("model", "logreg"),
        "basis": BASIS[mode],
        "settings": settings,
        "verification": VERIFICATION[mode],
        "validation": f"evaluated on {split}; subject-wise split hash, leakage detectors (split_integrity, "
        "sample_provenance) and metric recomputation are in the run record; per-subject results in "
        "per_subject.json",
        "applicability_limits": [*LIMITS[mode], "research use only; not a medical device, no diagnosis"],
    }
