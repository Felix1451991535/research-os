"""physio_eegmmi (ADR-179) on SYNTHETIC EDF+ files with T0/T1/T2 annotations; no download."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from researchos.sdk.conformance import DatasetProviderConformance
from researchos.sdk.testing import FakeBuildContext, FakeHttp

from researchos_plugin_physio_signals import edf, eegmmi
from synth import EEGMMI, eegmmi_files, mmi_run, tree_http

SETTINGS = {"subjects": ["S001", "S002", "S003", "S004", "S005", "S006"], "val_fraction": 0.2, "test_fraction": 0.2}


class TestEegMmi(DatasetProviderConformance):
    def make_provider(self) -> eegmmi.EegMmiProvider:
        return eegmmi.EegMmiProvider()

    def make_http(self) -> FakeHttp:
        return tree_http(EEGMMI, eegmmi_files())

    def settings(self) -> dict[str, Any]:
        return dict(SETTINGS)


def _build(settings: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    with tempfile.TemporaryDirectory() as td:
        d = eegmmi.EegMmiProvider().build(FakeBuildContext(Path(td), tree_http(EEGMMI, eegmmi_files())), settings)
        json.dumps(dict(d.extra))
        with np.load(Path(td) / d.table, allow_pickle=False) as t:
            return d, {k: t[k] for k in t.files}


def test_edf_plus_annotations_are_read() -> None:
    notes = edf.read_annotations(mmi_run(160, 1))
    assert [n[2] for n in notes[:4]] == ["T0", "T1", "T0", "T2"] and notes[1][0] == 4.2 and notes[1][1] == 4.1


def test_event_locked_windows_labels_rates_and_skips() -> None:
    d, t = _build(dict(SETTINGS))
    assert t["X"].shape[1:] == (3, 640) and set(t["y"].tolist()) == {0, 1}
    assert t["sampling_rate_hz"].tolist() == [160.0] * len(t["y"]) and d.extra["sampling_rate_hz"] == 160.0
    assert all(s.startswith("S006/") and "128" in r for s, r in d.extra["skipped_records"].items())  # 128 Hz
    assert "S006" not in set(t["group"].tolist()) and d.extra["label_map"] == {"0": "left_fist", "1": "right_fist"}
    assert d.egress == "cloud_ok" and d.license == "ODC-By-1.0"  # open access: may reach cloud models
    per_run = {s.split("/")[2] for s in t["sample_id"].tolist()}
    assert per_run == {f"S00{i}R{r:02d}.edf" for i in range(1, 6) for r in (4, 8, 12)}


def test_missing_channels_are_skipped_and_an_empty_result_is_an_error() -> None:
    import pytest

    with pytest.raises(ValueError, match="no samples were extracted"):
        _build({**SETTINGS, "channels": ["C3", "Oz"]})  # no record has Oz: every record is skipped


def test_channel_labels_ignore_padding_dots() -> None:
    assert eegmmi.channel_rows(("Fc5.", "C3..", "Cz..", "C4.."), ("c3", "CZ", "C4")) == [1, 2, 3]
    assert eegmmi.channel_rows(("C3..",), ("C4",)) is None
