"""HRV option of physio_ecg_baseline (BIOMED P2 step 2). Signals are SYNTHETIC (NeuroKit2 ecg_simulate, seeded) or
hand-built peak trains with known RR intervals, except the offline check on local MIT-BIH records."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from researchos.sdk.conformance import TemplateConformance

from researchos_plugin_physio_signals import mitbih, template_package, wfdb
from researchos_plugin_physio_signals.common import Table

sys.path.insert(0, str(template_package().root))
import hrv  # noqa: E402  (template-local module, as run.py imports it)

CFG = hrv.config({})


def _peaks(rr_ms: list[float], rate: float) -> np.ndarray[Any, Any]:
    return np.round(np.concatenate([[0.0], np.cumsum(rr_ms)]) * rate / 1000).astype(np.int64)


def test_match_peaks_is_one_to_one_within_tolerance() -> None:
    ref = np.array([100, 400, 700, 1000])
    assert hrv.match_peaks(ref, ref + 54, 54) == (4, 0, 0)  # the boundary is inside
    assert hrv.match_peaks(ref, ref + 55, 54) == (0, 4, 4)
    assert hrv.match_peaks(ref, np.array([101, 102, 700, 1300]), 54) == (2, 2, 2)  # a beat pairs once
    assert hrv.match_peaks(ref, np.empty(0, np.int64), 54) == (0, 4, 0)


def test_time_and_poincare_values_follow_the_hand_formulas() -> None:
    rr = [800.0, 850.0, 790.0, 900.0, 820.0, 760.0] * 5
    values, flags = hrv.rr_features(_peaks(rr, 1000.0), 1000.0)
    x = np.array(rr)
    assert values["HRV_MeanNN"] == pytest.approx(x.mean())
    assert values["HRV_SDNN"] == pytest.approx(x.std(ddof=1))
    assert values["HRV_RMSSD"] == pytest.approx(np.sqrt(np.mean(np.diff(x) ** 2)))
    # Task Force (1996): NN50 over the number of NN intervals, not over the successive differences
    assert values["HRV_pNN50"] == pytest.approx(100 * np.sum(np.abs(np.diff(x)) > 50) / len(x))
    # Poincare SD1/SD2 as in Brennan et al. (2001): spread across / along the identity line of (RR_n, RR_n+1)
    n, m = x[:-1], x[1:]
    assert values["HRV_SD1"] == pytest.approx(np.std((n - m) / np.sqrt(2), ddof=1))
    assert values["HRV_SD2"] == pytest.approx(np.std((n + m) / np.sqrt(2), ddof=1))
    assert "too_short_for_frequency" in flags and values["HRV_LF"] is None and values["HRV_HF"] is None


def test_hf_power_is_in_ms2() -> None:
    """RR = 1000 + 20 sin(2 pi 0.25 t) ms: HF power = 20^2 / 2 = 200 ms^2 (Welch leakage costs ~5%)."""
    t, rr = 0.0, []
    while t < 330:
        rr.append(1000 + 20 * np.sin(2 * np.pi * 0.25 * t))
        t += rr[-1] / 1000
    values, _ = hrv.rr_features(_peaks(rr, 1000.0), 1000.0)
    assert values["HRV_HF"] == pytest.approx(200, rel=0.1) and values["HRV_LF"] < 1


def test_unusable_segments_are_flagged_rows_not_zeros() -> None:
    ref = np.array([100, 400, -1, -1])
    flat = hrv.segment(np.zeros(3600), ref, 360.0, CFG)
    assert (
        flat["flags"] == ["flatline", "reference_mismatch"]
        and flat["quality"] == "flagged"
        and (flat["tp"], flat["fn"]) == (0, 2)
    )
    assert set(flat["values"].values()) == {None}
    noisy = hrv.segment(np.full(3600, np.nan), ref, 360.0, CFG)
    assert noisy["flags"] == ["nonfinite_samples", "reference_mismatch"]
    assert hrv.rr_features(np.array([0, 300, 600]), 360.0) == (dict.fromkeys(hrv.COLUMNS), ["insufficient_peaks"])


@cache
def _simulated(heart_rate: int, seed: int, seconds: int = 30, rate: int = 360) -> tuple[Any, Any]:
    import neurokit2 as nk

    ecg = nk.ecg_simulate(duration=seconds, sampling_rate=rate, heart_rate=heart_rate, random_state=seed)
    _, info = nk.ecg_peaks(nk.ecg_clean(ecg, sampling_rate=rate), sampling_rate=rate)
    return np.asarray(ecg), np.asarray(info["ECG_R_Peaks"])


def test_synthetic_ecg_recovers_the_simulated_heart_rate() -> None:
    ecg, _ = _simulated(72, 42)
    ref = np.full(120, -1)
    row = hrv.segment(ecg, ref, 360.0, CFG)
    assert row["values"]["HRV_MeanNN"] == pytest.approx(60_000 / 72, rel=0.02)
    assert row["n_detected"] == row["fp"] and row["n_reference"] == 0  # no reference beats: every detection is FP


def _split(rates: list[tuple[int, int]]) -> dict[str, Any]:
    xs, refs, ys, ids = [], [], [], []
    for i, (hr, label) in enumerate(rates):
        ecg, peaks = _simulated(hr, i)
        ref = np.full(120, -1)
        ref[: len(peaks)] = peaks + 7  # reference beats 19 ms off the detections: still matched
        xs.append(ecg)
        refs.append(ref)
        ys.append(label)
        ids.append(f"synthetic/{hr}/{i}")
    return {
        "X": np.stack(xs).astype(np.float32),
        "y": np.array(ys, dtype=np.int64),
        "group": np.array([f"s{i}" for i in range(len(ys))]),
        "sample_id": np.array(ids),
        "r_peaks": np.stack(refs),
        "sampling_rate_hz": np.full(len(ys), 360.0),
    }


class TestEcgBaselineHrv(TemplateConformance):
    def template_root(self) -> Path:
        return template_package().root

    def params(self) -> dict[str, Any]:
        return {"model": "logreg", "features": "neurokit_hrv"}

    def split_arrays(self) -> dict[str, dict[str, Any]]:
        return {
            "train": _split([(60, 0), (62, 0), (100, 1), (104, 1)]),
            "search_val": _split([(64, 0), (98, 1)]),
        }

    def test_logs_peak_counts_and_writes_the_hrv_table(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            self._prepare(rd)
            assert self._run(rd) == 0
            logged = [json.loads(x) for x in (rd / "metrics.jsonl").read_text().splitlines()]
            m = {r["name"]: r["value"] for r in logged if r["kind"] == "metric"}
            assert m["r_peak_fn"] == 0 and m["r_peak_fp"] == 0 and m["r_peak_sensitivity"] == 1.0
            table = json.loads((rd / "hrv_search_val.json").read_text())
            assert table["columns"]["HRV_LF"]["unit"] == "ms^2" and table["methods"]["correct_artifacts"] is False
            assert [r["sample_id"] for r in table["rows"]] == ["synthetic/64/0", "synthetic/98/1"]
            assert "too_short_for_frequency" in table["rows"][0]["flags"]


MITDB = Path(os.environ.get("MITBIH_DATA_DIR", "mitdb-not-configured"))


@pytest.mark.parametrize("record", ["100", "101", "103"])
def test_peak_detection_on_local_mitbih_records(record: str) -> None:
    """Regression guard on real data (measured 2026-10-16: sensitivity >= 0.998, PPV >= 0.998 per record with the
    neurokit method, 300 s segments). Offline only: the records are never downloaded or committed."""
    if not (MITDB / f"{record}.hea").is_file():
        pytest.skip(f"MIT-BIH record {record} not present (set MITBIH_DATA_DIR); no download")
    h = wfdb.parse_header((MITDB / f"{record}.hea").read_text())
    phys = wfdb.to_physical(
        h, wfdb.read_digital(h, {s.file_name: (MITDB / s.file_name).read_bytes() for s in h.signals})
    )
    table, stats = Table(), {"dropped_tail_samples": 0, "dropped_invalid": 0}
    anns = wfdb.read_annotations((MITDB / f"{record}.atr").read_bytes())
    mitbih.extract_segments(record, h, phys[:, mitbih.lead_index(h)], anns, table, stats, 300.0)
    rows = [hrv.segment(x, p, h.fs, CFG) for x, p in zip(table.X, table.extra["r_peaks"], strict=True)]
    tp, fn, fp = (sum(r[k] for r in rows) for k in ("tp", "fn", "fp"))
    assert tp / (tp + fn) >= 0.995 and tp / (tp + fp) >= 0.995


def test_pnn50_counts_exact_50_ms_steps_as_not_greater() -> None:
    """Regression (review #21): at 360 Hz an 18-sample step is exactly 50 ms; float ms conversion made it
    count as > 50 ms (96.7% instead of 0%). A 19-sample step (52.8 ms) counts."""
    exact = np.r_[0, np.cumsum([320, 338] * 15)]
    assert hrv.rr_features(exact, 360.0)[0]["HRV_pNN50"] == 0.0
    above = np.r_[0, np.cumsum([320, 339] * 15)]
    assert hrv.rr_features(above, 360.0)[0]["HRV_pNN50"] == pytest.approx(100 * 29 / 30)


def test_constant_rr_keeps_poincare_and_nulls_lf_hf() -> None:
    """Regression (review #22, #23): constant RR has SD1 = SD2 = 0 (not lost to unrelated nonlinear
    failures) and undefined LF/HF (not a roundoff ratio)."""
    values, flags = hrv.rr_features(np.arange(301) * 1000, 1000.0)
    for k in ("HRV_SD1", "HRV_SD2", "HRV_SDNN"):
        assert values[k] == pytest.approx(0.0, abs=1e-9), k  # float roundoff only
    assert values["HRV_LF"] is None and values["HRV_HF"] is None and values["HRV_LFHF"] is None
    assert "constant_rr" in flags


def test_time_domain_agrees_with_neurokit_on_irregular_rr() -> None:
    """Independent cross-check: the in-house time domain equals NeuroKit2's hrv_time away from the 50 ms boundary."""
    import neurokit2 as nk

    peaks = np.r_[0, np.cumsum(np.random.default_rng(3).integers(250, 400, 80))]
    ours = hrv.time_and_poincare(peaks, 360.0)
    theirs = nk.hrv_time(peaks, sampling_rate=360)
    for k in ("HRV_MeanNN", "HRV_SDNN", "HRV_RMSSD", "HRV_pNN50"):
        assert ours[k] == pytest.approx(float(theirs[k].iloc[0]), rel=1e-9), k


def test_match_tolerance_never_exceeds_150_ms() -> None:
    """Regression (review #26): round(0.150 * 250) = 38 samples admitted a 152 ms pair."""
    assert hrv.tolerance_samples(250.0) == 37 and hrv.tolerance_samples(360.0) == 54
    assert hrv.match_peaks(np.array([100]), np.array([138]), hrv.tolerance_samples(250.0)) == (0, 1, 1)
    assert hrv.match_peaks(np.array([100]), np.array([137]), hrv.tolerance_samples(250.0)) == (1, 0, 0)


def test_reference_mismatch_is_flagged_and_all_beat_codes_are_references() -> None:
    ecg, peaks = _simulated(72, 42)
    ref = np.full(120, -1)
    ref[: len(peaks) - 1] = peaks[:-1]  # one detected beat missing from the reference -> one FP
    row = hrv.segment(ecg, ref, 360.0, CFG)
    assert row["fp"] == 1 and "reference_mismatch" in row["flags"] and row["quality"] == "flagged"
    beat = wfdb.Annotation
    table, stats = Table(), {"dropped_tail_samples": 0, "dropped_invalid": 0}
    header = wfdb.Header("100", 1, 10.0, 100, (), ())
    anns = [beat(5, 1, "N"), beat(20, 1, "r"), beat(40, 1, "B"), beat(60, 1, "+")]
    mitbih.extract_segments("100", header, np.zeros(100), anns, table, stats, 10.0)
    assert table.extra["r_peaks"][0][:4].tolist() == [5, 20, 40, -1] and table.y == [0]  # labels stay AAMI-only
