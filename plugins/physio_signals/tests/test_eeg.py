"""EEG band-power option of the physio baseline (ADR-177). Signals are SYNTHETIC sinusoids/noise or synthetic
CHB-MIT tables from tests/synth.py; no real recordings."""

from __future__ import annotations

import json
import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from researchos.sdk.conformance import TemplateConformance

from researchos_plugin_physio_signals import chbmit, template_package
from synth import CHBMIT, chbmit_files, tree_http
from test_providers import CHB_SETTINGS
from test_template import _materialize

sys.path.insert(0, str(template_package().root))
import cards  # noqa: E402
import eeg  # noqa: E402

FS = 256.0


def windows(freq: float, n: int = 6, seconds: float = 4.0, channels: int = 2, seed: int = 0) -> dict[str, Any]:
    t = np.arange(int(seconds * FS)) / FS
    rng = np.random.default_rng(seed)
    x = np.stack(
        [[20 * np.sin(2 * np.pi * freq * t) + rng.normal(0, 2, t.size) for _ in range(channels)] for _ in range(n)]
    )
    return {"X": x, "sampling_rate_hz": np.full(n, FS)}


def test_a_10_hz_rhythm_lands_in_alpha() -> None:
    feats, rep = eeg.extract(windows(10.0), eeg.config({}))
    bands = rep["bands_used"]
    assert bands == ["delta", "theta", "alpha", "beta", "gamma"]
    per = feats.reshape(6, 2, len(bands))
    assert (per.argmax(axis=2) == bands.index("alpha")).all()


def test_artifacts_and_flat_channels_are_reported_not_dropped() -> None:
    data = windows(10.0)
    data["X"][1, 0, 100] += 5000.0  # a 5 mV spike
    data["X"][2, 1, :] = 3.0  # a flat channel
    feats, rep = eeg.extract(data, eeg.config({}))
    assert len(feats) == 6 and rep["high_amplitude_windows"] == 1 and rep["windows_with_flat_channels"] == 1
    assert rep["high_amplitude_mask"][1] is True and rep["flat_channels"][2] == 1


def test_unresolvable_bands_and_bad_rates_are_explicit() -> None:
    _, rep = eeg.extract(windows(10.0, seconds=0.5), eeg.config({}))
    assert "delta" in rep["bands_unresolved"] and rep["frequency_resolution_hz"] == pytest.approx(2.0)
    mixed = windows(10.0)
    mixed["sampling_rate_hz"][0] = 128.0
    with pytest.raises(ValueError, match="different sampling rates"):
        eeg.extract(mixed, eeg.config({}))
    with pytest.raises(ValueError, match="Nyquist"):
        eeg.extract(windows(10.0), eeg.config({"h_freq": 200.0}))
    with pytest.raises(ValueError, match="sampling_rate_hz"):
        eeg.extract({"X": windows(10.0)["X"]}, eeg.config({}))


def test_per_subject_balanced_accuracy() -> None:
    y = np.array([0, 0, 1, 1, 0, 0])
    pred = np.array([0, 1, 1, 1, 0, 0])
    g = np.array(["a", "a", "a", "a", "b", "b"])
    out = cards.per_subject(y, pred, g)
    assert out["a"] == {"n": 4, "classes": 2, "balanced_accuracy": 0.75} and out["b"]["balanced_accuracy"] == 1.0


@cache
def chb_splits() -> dict[str, Any]:
    http = tree_http(CHBMIT, chbmit_files(), sums=False)
    return _materialize(
        chbmit.ChbMitProvider(), http, dict(CHB_SETTINGS), {"train": "train", "search_val": "search_val"}
    )


class TestEegBandpower(TemplateConformance):
    def template_root(self) -> Path:
        return template_package().root

    def params(self) -> dict[str, Any]:
        return {"model": "logreg", "features": "mne_bandpower", "l_freq": 1.0, "h_freq": 12.0}  # synthetic fs = 32 Hz

    def split_arrays(self) -> dict[str, dict[str, Any]]:
        return chb_splits()

    def test_writes_model_card_per_subject_and_eeg_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            self._prepare(rd)
            assert self._run(rd) == 0
            card = json.loads((rd / "model_card.json").read_text())
            assert card["features"] == "mne_bandpower" and card["applicability_limits"] and card["verification"]
            assert "research use only" in " ".join(card["applicability_limits"])
            subjects = json.loads((rd / "per_subject.json").read_text())["subjects"]
            assert set(subjects) == set(self.split_arrays()["search_val"]["group"].tolist())
            rep = json.loads((rd / "eeg_search_val.json").read_text())
            assert rep["windows"] == len(self.split_arrays()["search_val"]["y"]) and rep["bands_used"]


@cache
def mmi_splits() -> dict[str, Any]:
    from researchos_plugin_physio_signals import eegmmi
    from synth import EEGMMI, eegmmi_files

    settings = {"subjects": ["S001", "S002", "S003", "S004", "S005"], "val_fraction": 0.2, "test_fraction": 0.2}
    return _materialize(
        eegmmi.EegMmiProvider(),
        tree_http(EEGMMI, eegmmi_files(slow=())),
        settings,
        {"train": "train", "search_val": "search_val"},
    )


class TestEegMmiDecoding(TemplateConformance):
    """End-to-end shape on SYNTHETIC EEGMMI windows: subject-wise split, MNE band power, per-subject results. The
    planted contralateral alpha difference must be decodable (a pipeline check, not a claim about real EEG)."""

    def template_root(self) -> Path:
        return template_package().root

    def params(self) -> dict[str, Any]:
        return {"model": "logreg", "features": "mne_bandpower"}

    def split_arrays(self) -> dict[str, dict[str, Any]]:
        return mmi_splits()

    def test_planted_lateralization_is_decoded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            self._prepare(rd)
            assert self._run(rd) == 0
            metrics = [json.loads(x) for x in (rd / "metrics.jsonl").read_text().splitlines()]
            f1 = next(m["value"] for m in metrics if m.get("name") == "macro_f1" and m["kind"] == "metric")
            subjects = json.loads((rd / "per_subject.json").read_text())["subjects"]
            assert f1 > 0.8 and subjects and all(s["classes"] == 2 for s in subjects.values())


def _mmi_arrays() -> tuple[dict[str, Any], dict[str, Any]]:
    s = mmi_splits()
    return dict(s["train"]), dict(s["search_val"])


def test_csp_filters_are_fit_on_train_only() -> None:
    """Leakage check (ADR-186): changing the evaluation windows changes nothing learned from training."""
    tr, ev = _mmi_arrays()
    cfg = eeg.csp_config({})
    x_tr, x_ev, keep, _, _ = eeg.csp(tr, ev, cfg)
    noisy = {**ev, "X": np.random.default_rng(1).normal(0, 50, np.shape(ev["X"])), "y": np.zeros(len(ev["y"]))}
    x_tr2, x_ev2, _, _, _ = eeg.csp(tr, noisy, cfg)
    assert np.array_equal(x_tr, x_tr2) and keep.all()
    assert x_ev.shape == (len(ev["y"]), 3) and not np.allclose(x_ev, x_ev2)  # min(4, 3 channels) components


def test_csp_errors_are_explicit() -> None:
    tr, ev = _mmi_arrays()
    with pytest.raises(ValueError, match="csp_components"):
        eeg.csp(tr, ev, eeg.csp_config({"csp_components": 99}))
    with pytest.raises(ValueError, match="two classes"):
        eeg.csp({**tr, "y": np.zeros(len(tr["y"]))}, ev, eeg.csp_config({}))
    with pytest.raises(ValueError, match="Nyquist"):
        eeg.csp(tr, ev, eeg.csp_config({"h_freq": 90.0}))


class TestEegMmiCsp(TestEegMmiDecoding):
    """Same SYNTHETIC EEGMMI windows through CSP (motor-imagery BCI baseline)."""

    def params(self) -> dict[str, Any]:
        return {"model": "logreg", "features": "mne_csp"}

    def test_model_card_names_the_train_only_fit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            self._prepare(rd)
            assert self._run(rd) == 0
            card = json.loads((rd / "model_card.json").read_text())
            assert card["features"] == "mne_csp" and "train" in card["basis"]
            assert card["settings"]["csp_components"] == 3  # effective value, not the unset default
