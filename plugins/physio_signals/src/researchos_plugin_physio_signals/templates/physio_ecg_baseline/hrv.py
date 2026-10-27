"""HRV features for physio_ecg_baseline (`features: "neurokit_hrv"`). Template-local, so the reviewed template hash
covers it. Research use only; no diagnostic claim.

Input: continuous ECG segments X[N, T] with per-row `sampling_rate_hz` and reference `r_peaks` (-1 padded), as built
by physio_mitbih `unit: "segment"`. Per segment: NeuroKit2 cleaning + R-peak detection (methods recorded, no
artifact correction), one-to-one match against the reference peaks within 150 ms, HRV from the DETECTED peaks.
A value that cannot be computed is null with a quality flag, never 0. Time domain and SD1/SD2 are computed here from
integer sample intervals; NeuroKit2 is used for cleaning, peak detection and LF/HF. Frequency-domain power is in ms^2
only with Welch PSD and normalize=False (NeuroKit2 defaults return a different scale), so those settings are fixed.
"""

from __future__ import annotations

import importlib.metadata
import math
import warnings
from typing import Any

import numpy as np

Array = np.ndarray[Any, Any]

NEUROKIT_VERSION = "0.2.13"
TOLERANCE_S = 0.150  # beat-match window (AAMI EC57 practice)
METHODS = ("neurokit", "pantompkins1985")
FREQUENCY = {"psd_method": "welch", "normalize": False, "interpolation_rate": 4}
MIN_FREQUENCY_S = 270.0  # detected-peak span needed for LF (0.04 Hz -> >= ~10 cycles)
MIN_NONLINEAR_BEATS = 20
COLUMNS: dict[str, dict[str, str]] = {
    "HRV_MeanNN": {"unit": "ms", "domain": "time"},
    "HRV_SDNN": {"unit": "ms", "domain": "time"},
    "HRV_RMSSD": {"unit": "ms", "domain": "time"},
    "HRV_pNN50": {"unit": "%", "domain": "time"},
    "HRV_LF": {"unit": "ms^2", "domain": "frequency"},
    "HRV_HF": {"unit": "ms^2", "domain": "frequency"},
    "HRV_LFHF": {"unit": "1", "domain": "frequency"},
    "HRV_SD1": {"unit": "ms", "domain": "nonlinear"},
    "HRV_SD2": {"unit": "ms", "domain": "nonlinear"},
}


def neurokit() -> Any:
    found = importlib.metadata.version("neurokit2")
    if found != NEUROKIT_VERSION:
        raise RuntimeError(f"neurokit2=={NEUROKIT_VERSION} required (found {found}); results would differ")
    import neurokit2

    return neurokit2


def config(params: dict[str, Any]) -> dict[str, Any]:
    clean, peaks = params.get("ecg_clean_method", "neurokit"), params.get("ecg_peaks_method", "neurokit")
    return {
        "neurokit2": NEUROKIT_VERSION,
        "ecg_clean_method": clean,
        "ecg_peaks_method": peaks,
        "correct_artifacts": False,
        "match_tolerance_s": TOLERANCE_S,
        "frequency": FREQUENCY,
        "lf_band_hz": [0.04, 0.15],
        "hf_band_hz": [0.15, 0.4],
        "min_frequency_span_s": MIN_FREQUENCY_S,
        "min_nonlinear_beats": MIN_NONLINEAR_BEATS,
        "rr_note": "detected RR intervals, not certified NN intervals (no ectopy removal)",
    }


def tolerance_samples(rate: float) -> int:
    """Largest whole-sample distance within TOLERANCE_S (review #26: round() admitted 38 samples = 152 ms
    at 250 Hz). The epsilon keeps an exact boundary (54 samples at 360 Hz) despite float products."""
    return math.floor(TOLERANCE_S * rate + 1e-9)


def match_peaks(reference: Array, detected: Array, tolerance: int) -> tuple[int, int, int]:
    """(TP, FN, FP) of a one-to-one, in-order match: a detection within `tolerance` samples of a reference beat
    pairs with it; each beat and each detection is used once. Exact while RR > 2 * tolerance."""
    ref, det = np.sort(reference), np.sort(detected)
    i = j = tp = 0
    while i < len(ref) and j < len(det):
        if abs(int(det[j]) - int(ref[i])) <= tolerance:
            tp, i, j = tp + 1, i + 1, j + 1
        elif det[j] < ref[i]:
            j += 1
        else:
            i += 1
    return tp, len(ref) - tp, len(det) - tp


def time_and_poincare(peaks: Array, rate: float) -> dict[str, float | None]:
    """Time domain and Poincare SD1/SD2 from the integer peak samples (review #21, #22). pNN50 compares
    |successive RR difference| > 50 ms as 1000 |d| > 50 rate on integer sample counts, so an exact 50 ms step is
    not counted through float roundoff; denominator = number of NN intervals (Task Force 1996). SD1/SD2 follow
    Brennan et al. (2001) with ddof=1 and need MIN_NONLINEAR_BEATS beats; they no longer depend on NeuroKit2's
    other nonlinear indices."""
    steps = np.diff(peaks.astype(np.int64))
    rr = steps * 1000.0 / rate
    out: dict[str, float | None] = {
        "HRV_MeanNN": float(rr.mean()),
        "HRV_SDNN": float(rr.std(ddof=1)),
        "HRV_RMSSD": float(np.sqrt(np.mean(np.diff(rr) ** 2))),
        "HRV_pNN50": float(100.0 * np.count_nonzero(np.abs(np.diff(steps)) * 1000 > 50 * rate) / len(rr)),
    }
    if len(peaks) >= MIN_NONLINEAR_BEATS:
        a, b = rr[:-1], rr[1:]
        out["HRV_SD1"] = float(np.std((a - b) / np.sqrt(2), ddof=1))
        out["HRV_SD2"] = float(np.std((a + b) / np.sqrt(2), ddof=1))
    return out


def frequency(peaks: Array, rate: float, flags: list[str]) -> dict[str, float | None]:
    """LF/HF power (ms^2) from NeuroKit2 with the fixed Welch settings; null with a flag when RR is constant or HF
    is numerically zero (review #23: roundoff gave LF/HF = 1.71 for a constant RR)."""
    none: dict[str, float | None] = {"HRV_LF": None, "HRV_HF": None, "HRV_LFHF": None}
    if np.ptp(np.diff(peaks)) == 0:
        flags.append("constant_rr")
        return none
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = neurokit().hrv_frequency(peaks, sampling_rate=rate, **FREQUENCY)
        except (ValueError, ZeroDivisionError, IndexError) as exc:
            flags.append(f"frequency_failed:{type(exc).__name__}")
            return none
    if caught:
        flags.append("frequency_warnings")
    lf, hf = (float(result[k].iloc[0]) for k in ("HRV_LF", "HRV_HF"))
    out = {"HRV_LF": lf if np.isfinite(lf) else None, "HRV_HF": hf if np.isfinite(hf) else None, "HRV_LFHF": None}
    if out["HRV_LF"] is not None and out["HRV_HF"] is not None and hf > 1e-9:
        out["HRV_LFHF"] = lf / hf
    else:
        flags.append("lfhf_undefined")
    return out


def rr_features(peaks: Array, rate: float) -> tuple[dict[str, float | None], list[str]]:
    values: dict[str, float | None] = dict.fromkeys(COLUMNS)
    if len(peaks) < 4:
        return values, ["insufficient_peaks"]
    flags = []
    rr = np.diff(peaks) / rate
    if np.any((rr < 0.3) | (rr > 2.0)):
        flags.append("rr_outside_300_2000_ms")
    values.update(time_and_poincare(peaks, rate))
    if len(peaks) < MIN_NONLINEAR_BEATS:
        flags.append("too_few_beats_for_nonlinear")
    if (peaks[-1] - peaks[0]) / rate >= MIN_FREQUENCY_S:
        values.update(frequency(peaks, rate, flags))
    else:
        flags.append("too_short_for_frequency")
    return values, flags


def segment(signal: Array, reference: Array, rate: float, cfg: dict[str, Any]) -> dict[str, Any]:
    """One HRV row for one continuous segment."""
    row: dict[str, Any] = {"values": dict.fromkeys(COLUMNS), "flags": [], "n_detected": 0}
    ref = reference[reference >= 0]
    if not np.isfinite(signal).all():
        row["flags"] = ["nonfinite_samples"]
    elif np.ptp(signal) <= np.finfo(float).eps:
        row["flags"] = ["flatline"]
    else:
        nk = neurokit()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            clean = nk.ecg_clean(signal, sampling_rate=rate, method=cfg["ecg_clean_method"])
            _, info = nk.ecg_peaks(clean, sampling_rate=rate, method=cfg["ecg_peaks_method"], correct_artifacts=False)
        peaks = np.asarray(info["ECG_R_Peaks"], dtype=np.int64)
        row["n_detected"] = len(peaks)
        row["values"], row["flags"] = rr_features(peaks, rate)
        row["peaks"] = peaks
    tp, fn, fp = match_peaks(ref, row.pop("peaks", np.empty(0, np.int64)), tolerance_samples(rate))
    row.update(n_reference=len(ref), tp=tp, fn=fn, fp=fp)
    if len(ref) and (fn or fp):
        row["flags"].append("reference_mismatch")
    row["quality"] = "flagged" if row["flags"] else "unflagged_not_validated"
    return row


def extract(data: Any, cfg: dict[str, Any]) -> tuple[Array, list[dict[str, Any]]]:
    """Feature matrix [N, len(COLUMNS)] (NaN where unavailable) and one artifact row per segment."""
    files = set(getattr(data, "files", data))
    if not {"r_peaks", "sampling_rate_hz"} <= files:
        raise ValueError(
            "neurokit_hrv needs continuous segments with r_peaks and sampling_rate_hz "
            '(build physio_mitbih with unit "segment")'
        )
    X, refs, rates = np.asarray(data["X"], float), np.asarray(data["r_peaks"]), np.asarray(data["sampling_rate_hz"])
    if X.ndim != 2:
        raise ValueError("neurokit_hrv needs single-lead segments X[N, T]")
    rows = []
    for sid, signal, ref, rate in zip(data["sample_id"], X, refs, rates, strict=True):
        rows.append({"sample_id": str(sid), **segment(signal, ref, float(rate), cfg)})
    matrix = np.array([[np.nan if r["values"][k] is None else r["values"][k] for k in COLUMNS] for r in rows], float)
    return matrix.reshape(len(rows), len(COLUMNS)), rows
