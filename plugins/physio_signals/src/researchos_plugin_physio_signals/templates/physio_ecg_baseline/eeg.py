"""EEG features for the physio baseline: band power (`features: "mne_bandpower"`, ADR-177) and common spatial
patterns for motor-imagery BCI (`features: "mne_csp"`, ADR-186). Template-local, so the reviewed template hash covers
it. Research use only.

CSP is supervised: its spatial filters are fit on the TRAINING windows (and labels) only, then applied unchanged to
the evaluation windows - the evaluation split never influences the filters.

Input: windows X[N, channels, samples] with a per-row `sampling_rate_hz` (physio_chbmit). Per window: MNE zero-phase
FIR band-pass, artifact checks (peak-to-peak above `artifact_ptp`, flat channels) - reported, never silently dropped -
then Welch PSD (`mne.time_frequency.psd_array_welch`) and log10 mean power per channel and band. A band is used only
if the window resolves it (at least two frequency bins inside it) and it lies below the low-pass edge.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any

import numpy as np

Array = np.ndarray[Any, Any]

MNE_VERSION = "1.10.2"
BANDS = {"delta": (1.0, 4.0), "theta": (4.0, 8.0), "alpha": (8.0, 13.0), "beta": (13.0, 30.0), "gamma": (30.0, 45.0)}
FLAT = 1e-6  # a channel whose peak-to-peak is below this (data units) carries no signal


def mne() -> Any:
    found = importlib.metadata.version("mne")
    if found != MNE_VERSION:
        raise RuntimeError(f"mne=={MNE_VERSION} required (found {found}); results would differ")
    import mne as m

    return m


def config(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "mne": MNE_VERSION,
        "l_freq": float(params.get("l_freq", 1.0)),
        "h_freq": float(params.get("h_freq", 45.0)),
        "filter": "MNE FIR, zero-phase (filter_data defaults: firwin, hamming)",
        "psd": "Welch (psd_array_welch), window = min(samples, 2 s), 50% overlap",
        "artifact_ptp": float(params.get("artifact_ptp", 500.0)),
        "exclude_artifacts_train": bool(params.get("exclude_artifacts_train", False)),
        "bands_hz": BANDS,
    }


def _rate(data: Any) -> float:
    if "sampling_rate_hz" not in getattr(data, "files", data):
        raise ValueError("mne_bandpower needs per-row sampling_rate_hz (build physio_chbmit, ADR-177)")
    rates = np.unique(np.asarray(data["sampling_rate_hz"], float))
    if len(rates) != 1:
        raise ValueError(f"windows have different sampling rates {rates.tolist()}; resample before decoding")
    return float(rates[0])


def artifacts(x: Array, ptp_max: float) -> tuple[Array, Array]:
    """(high-amplitude window mask, flat-channel count per window)."""
    ptp = np.ptp(x, axis=2)
    return (ptp > ptp_max).any(axis=1), (ptp < FLAT).sum(axis=1)


def bands_for(freqs: Array, h_freq: float) -> dict[str, Array]:
    usable = {}
    for name, (lo, hi) in BANDS.items():
        mask = (freqs >= lo) & (freqs < hi)
        if hi <= h_freq and mask.sum() >= 2:
            usable[name] = mask
    return usable


def extract(data: Any, cfg: dict[str, Any]) -> tuple[Array, dict[str, Any]]:
    """Features [N, channels x usable bands] and a report (bands, artifact counts, per-window flags)."""
    x = np.asarray(data["X"], dtype=np.float64)
    if x.ndim != 3:
        raise ValueError("mne_bandpower needs X[N, channels, samples]")
    fs = _rate(data)
    if not cfg["l_freq"] < cfg["h_freq"] < fs / 2:
        raise ValueError(f"band-pass {cfg['l_freq']}-{cfg['h_freq']} Hz needs h_freq below Nyquist ({fs / 2} Hz)")
    m = mne()
    high, flat = artifacts(x, cfg["artifact_ptp"])
    filtered = m.filter.filter_data(x, fs, cfg["l_freq"], cfg["h_freq"], verbose=False)
    n_fft = int(min(x.shape[2], 2 * fs))
    psd, freqs = m.time_frequency.psd_array_welch(
        filtered,
        fs,
        fmin=cfg["l_freq"],
        fmax=cfg["h_freq"],
        n_fft=n_fft,
        n_per_seg=n_fft,
        n_overlap=n_fft // 2,
        verbose=False,
    )
    usable = bands_for(freqs, cfg["h_freq"])
    if not usable:
        raise ValueError(f"windows of {x.shape[2] / fs:.2f} s resolve no band; use longer windows")
    power = np.stack([psd[:, :, mask].mean(axis=2) for mask in usable.values()], axis=2)
    feats = np.log10(power + np.finfo(float).tiny).reshape(len(x), -1)
    report = {
        "sampling_rate_hz": fs,
        "bands_used": list(usable),
        "bands_unresolved": sorted(set(BANDS) - set(usable)),
        "frequency_resolution_hz": float(freqs[1] - freqs[0]) if len(freqs) > 1 else None,
        "high_amplitude_windows": int(high.sum()),
        "windows_with_flat_channels": int((flat > 0).sum()),
        "windows": len(x),
    }
    return feats, {**report, "high_amplitude_mask": high.tolist(), "flat_channels": flat.tolist()}


def csp_config(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "mne": MNE_VERSION,
        "l_freq": float(params.get("l_freq", 8.0)),
        "h_freq": float(params.get("h_freq", 30.0)),
        "filter": "MNE FIR, zero-phase (filter_data defaults: firwin, hamming)",
        "csp_components": params.get("csp_components"),  # default min(4, channels), recorded in the report
        "csp_reg": params.get("csp_reg"),
        "csp": "mne.decoding.CSP(log=True): log-variance of the spatially filtered windows; fit on train only",
        "artifact_ptp": float(params.get("artifact_ptp", 500.0)),
        "exclude_artifacts_train": bool(params.get("exclude_artifacts_train", False)),
    }


def csp(tr: Any, ev: Any, cfg: dict[str, Any]) -> tuple[Array, Array, Array, dict[str, Any], dict[str, Any]]:
    """(train features, eval features, train keep-mask, train report, eval report). Filters fit on kept train only."""
    x_tr, x_ev = (np.asarray(d["X"], dtype=np.float64) for d in (tr, ev))
    if x_tr.ndim != 3 or x_ev.ndim != 3:
        raise ValueError("mne_csp needs X[N, channels, samples]")
    fs = _rate(tr)
    if _rate(ev) != fs:
        raise ValueError("train and evaluation windows have different sampling rates")
    if not cfg["l_freq"] < cfg["h_freq"] < fs / 2:
        raise ValueError(f"band-pass {cfg['l_freq']}-{cfg['h_freq']} Hz needs h_freq below Nyquist ({fs / 2} Hz)")
    n_comp = int(cfg["csp_components"] or min(4, x_tr.shape[1]))
    if not 1 <= n_comp <= x_tr.shape[1]:
        raise ValueError(f"csp_components must be 1..{x_tr.shape[1]} (channels)")
    y_tr = np.asarray(tr["y"])
    m = mne()
    reports = []
    for x in (x_tr, x_ev):
        high, flat = artifacts(x, cfg["artifact_ptp"])
        reports.append(
            {
                "sampling_rate_hz": fs,
                "windows": len(x),
                "high_amplitude_windows": int(high.sum()),
                "windows_with_flat_channels": int((flat > 0).sum()),
                "high_amplitude_mask": high.tolist(),
                "flat_channels": flat.tolist(),
            }
        )
    high_tr = np.asarray(reports[0]["high_amplitude_mask"])
    keep = ~high_tr if cfg["exclude_artifacts_train"] else np.ones(len(x_tr), bool)
    if len(set(y_tr[keep].tolist())) < 2:
        raise ValueError("CSP needs at least two classes among the training windows")
    f_tr = m.filter.filter_data(x_tr, fs, cfg["l_freq"], cfg["h_freq"], verbose=False)
    f_ev = m.filter.filter_data(x_ev, fs, cfg["l_freq"], cfg["h_freq"], verbose=False)
    model = m.decoding.CSP(n_components=n_comp, reg=cfg["csp_reg"], log=True)
    reports[0]["csp_components"] = n_comp
    with m.utils.use_log_level("WARNING"):
        model.fit(f_tr[keep], y_tr[keep])
        return np.asarray(model.transform(f_tr)), np.asarray(model.transform(f_ev)), keep, reports[0], reports[1]
