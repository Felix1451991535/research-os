"""WFDB / EDF parser tests on tiny SYNTHETIC records, including corrupt and truncated files."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from researchos_plugin_physio_signals import edf, wfdb
from synth import EEG_LABELS, edf_bytes, enc16, enc212, enc_atr, mit_record, wfdb_header

FIX = Path(__file__).parents[1] / "fixtures" / "synthetic_wfdb"


def _frozen() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in FIX.iterdir() if p.suffix in (".hea", ".dat", ".atr")}


def test_frozen_fixture_matches_generator() -> None:
    assert _frozen() == mit_record("sy100", seed=7, seconds=4.0)


def test_header_fields() -> None:
    h = wfdb.parse_header(_frozen()["sy100.hea"].decode())
    assert (h.record, h.n_signals, h.fs, h.n_samples) == ("sy100", 2, 360.0, 1440)
    s = h.signals[0]
    assert (s.file_name, s.fmt, s.gain, s.baseline, s.adc_res, s.description) == (
        "sy100.dat",
        212,
        200.0,
        1024,
        12,
        "MLII",
    )
    assert s.units is None  # not stated in the header -> unknown, not a guessed default
    assert h.comments == ("SYNTHETIC record",)


def test_ptb_style_gain_baseline_units() -> None:
    dig = np.arange(-6, 6).reshape(6, 2)
    h = wfdb.parse_header(wfdb_header("x", 100, dig, "x.dat", 16, ["I", "II"], 1000.0, 0, style="ptb"))
    assert (h.signals[0].gain, h.signals[0].baseline, h.signals[0].units) == (1000.0, 0, "mV")
    phys = wfdb.to_physical(h, wfdb.read_digital(h, {"x.dat": enc16(dig.reshape(-1))}))
    assert phys.dtype == np.float32 and np.allclose(phys, dig / 1000.0)


def test_212_roundtrip_including_negative_and_odd_count() -> None:
    vals = np.array([0, 1, -1, 2047, -2047, 1000, -1000])
    dig = vals.reshape(-1, 1)
    h = wfdb.parse_header(wfdb_header("o", 360, dig, "o.dat", 212, ["MLII"], 200, 0))
    out = wfdb.read_digital(h, {"o.dat": enc212(vals)})
    assert out[:, 0].tolist() == vals.tolist()


def test_signal_values_and_invalid_samples() -> None:
    files = _frozen()
    h = wfdb.parse_header(files["sy100.hea"].decode())
    dig = wfdb.read_digital(h, files)
    assert dig.shape == (1440, 2)
    phys = wfdb.to_physical(h, dig)
    assert np.allclose(phys, (dig - 1024) / 200.0)
    bad = dig.copy()
    bad[5, 0] = -2048
    assert np.isnan(wfdb.to_physical(h, bad)[5, 0])


def test_truncated_dat_is_an_error() -> None:
    files = _frozen()
    h = wfdb.parse_header(files["sy100.hea"].decode())
    with pytest.raises(wfdb.WfdbError, match="truncated"):
        wfdb.read_digital(h, {"sy100.dat": files["sy100.dat"][:-30]})


def test_corrupt_dat_fails_checksum() -> None:
    files = _frozen()
    h = wfdb.parse_header(files["sy100.hea"].decode())
    data = bytearray(files["sy100.dat"])
    data[300] ^= 0x5A
    with pytest.raises(wfdb.WfdbError, match="checksum"):
        wfdb.read_digital(h, {"sy100.dat": bytes(data)})


def test_header_errors() -> None:
    for text, msg in (
        ("", "empty"),
        ("r 1 360 10\n", "declares 1 signals"),
        ("r/2 1 360\n", "multi-segment"),
        ("r 1 360 10\nr.dat 80 200 12 0 0 0 0 x\n", "format 80"),
        ("r 1 360\nr.dat 212 200 12 0 0 0 0 x\n", None),
    ):
        if msg is None:
            h = wfdb.parse_header(text)
            with pytest.raises(wfdb.WfdbError, match="sample count missing"):
                wfdb.read_digital(h, {"r.dat": b""})
        else:
            with pytest.raises(wfdb.WfdbError, match=msg):
                wfdb.parse_header(text)


def test_annotations_symbols_aux_and_skip() -> None:
    anns = wfdb.read_annotations(enc_atr([(0, 28), (10, 1), (5000, 5), (5100, 8)], {0: "(AFIB"}))
    assert [(a.sample, a.symbol) for a in anns] == [(0, "+"), (10, "N"), (5000, "V"), (5100, "A")]
    assert anns[0].aux == "(AFIB" and anns[1].aux is None


def test_frozen_annotations() -> None:
    anns = wfdb.read_annotations(_frozen()["sy100.atr"])
    assert anns[0].symbol == "+" and [a.symbol for a in anns[1:4]] == ["N", "N", "V"]


def test_truncated_annotations_are_an_error() -> None:
    data = enc_atr([(10, 1), (20, 1)])
    with pytest.raises(wfdb.WfdbError, match="no end-of-file"):
        wfdb.read_annotations(data[:-2])
    with pytest.raises(wfdb.WfdbError, match="odd length"):
        wfdb.read_annotations(data[:-1])


def test_edf_roundtrip_and_annotation_channel_excluded() -> None:
    sig = np.vstack([np.linspace(-100, 100, 64), np.zeros(64)])
    raw = edf_bytes(["FP1-F7", "EDF Annotations"], 16, sig)
    hdr, out = edf.read_edf(raw)
    assert hdr.labels == ("FP1-F7", "EDF Annotations") and edf.signal_indices(hdr) == [0]
    assert out.shape == (1, 64) and edf.sampling_rate(hdr) == 16.0
    assert np.allclose(out[0], sig[0], atol=6400 / 65535)
    assert hdr.phys_dim[0] == "uV"


def test_edf_truncated_and_bad_header() -> None:
    raw = edf_bytes(EEG_LABELS, 32, np.zeros((len(EEG_LABELS), 64)))
    with pytest.raises(edf.EdfError, match="data section"):
        edf.read_edf(raw[:-10])
    with pytest.raises(edf.EdfError, match="truncated"):
        edf.read_edf(raw[:100])
    with pytest.raises(edf.EdfError, match="not numeric"):
        edf.read_edf(raw[:252] + b"xx  " + raw[256:])
