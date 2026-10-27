"""DatasetProviderConformance per provider + leakage, checksum, license, cancel and ranged-download tests.
All upstream content is SYNTHETIC (tests/synth.py), served by researchos.sdk.testing.FakeHttp."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from researchos.contracts.datasources import DatasetDescriptor
from researchos.contracts.http import HttpResponse
from researchos.sdk.conformance import DatasetProviderConformance
from researchos.sdk.testing import FakeBuildContext, FakeHttp

from researchos_plugin_physio_signals import chbmit, mitbih, ptbxl, wfdb
from researchos_plugin_physio_signals.common import BuildCancelled, LeakageError, Table, seeded_partition, split_spec
from researchos_plugin_physio_signals.fetch import UpstreamError, detect_license, fetch_bytes
from synth import CC_BY, CHBMIT, EEG_CHANNELS, MITDB, PTB_ROWS, PTBXL, chbmit_files, mitdb_files, ptbxl_files, tree_http

MIT_SETTINGS = {"records": ["101", "106", "108", "109", "112", "100", "103", "105"], "val_fraction": 0.2}
CHB_SETTINGS = {
    "subjects": ["chb01", "chb02", "chb03", "chb04", "chb21"],
    "channels": EEG_CHANNELS,
    "max_background_windows_per_record": 5,
    "test_fraction": 0.25,
    "val_fraction": 0.25,
}


class TestMitBih(DatasetProviderConformance):
    def make_provider(self) -> mitbih.MitBihProvider:
        return mitbih.MitBihProvider()

    def make_http(self) -> FakeHttp:
        return tree_http(MITDB, mitdb_files())

    def settings(self) -> dict[str, Any]:
        return dict(MIT_SETTINGS)


class TestMitBihSegments(TestMitBih):
    def settings(self) -> dict[str, Any]:
        return {**MIT_SETTINGS, "unit": "segment", "segment_s": 10}


class TestPtbXl(DatasetProviderConformance):
    def make_provider(self) -> ptbxl.PtbXlProvider:
        return ptbxl.PtbXlProvider()

    def make_http(self) -> FakeHttp:
        return tree_http(PTBXL, ptbxl_files(), license_text=CC_BY)


class TestChbMit(DatasetProviderConformance):
    def make_provider(self) -> chbmit.ChbMitProvider:
        return chbmit.ChbMitProvider()

    def make_http(self) -> FakeHttp:
        return tree_http(CHBMIT, chbmit_files(), sums=False)

    def settings(self) -> dict[str, Any]:
        return dict(CHB_SETTINGS)


Build = Callable[[], tuple[DatasetDescriptor, dict[str, Any]]]


def _build(provider: Any, http: FakeHttp, settings: dict[str, Any]) -> tuple[DatasetDescriptor, dict[str, Any]]:
    with tempfile.TemporaryDirectory() as td:
        d = provider.build(FakeBuildContext(Path(td), http), settings)
        json.dumps(dict(d.extra))  # the host writes extra into manifest.json
        with np.load(Path(td) / d.table, allow_pickle=False) as t:
            arrays = {k: t[k] for k in t.files}
    return d, arrays


BUILDS: dict[str, Build] = {
    "mitbih": lambda: _build(mitbih.MitBihProvider(), tree_http(MITDB, mitdb_files()), dict(MIT_SETTINGS)),
    "ptbxl": lambda: _build(ptbxl.PtbXlProvider(), tree_http(PTBXL, ptbxl_files(), license_text=CC_BY), {}),
    "chbmit": lambda: _build(
        chbmit.ChbMitProvider(), tree_http(CHBMIT, chbmit_files(), sums=False), dict(CHB_SETTINGS)
    ),
}


@pytest.mark.parametrize("name", sorted(BUILDS))
def test_no_subject_in_more_than_one_split(name: str) -> None:
    d, t = BUILDS[name]()
    assign = d.extra["split"]["assign"]
    owner: dict[str, str] = {}
    for split, groups in assign.items():
        for g in groups:
            assert g not in owner, f"{g} in {owner.get(g)} and {split}"
            owner[g] = split
    rows_per_split: dict[str, set[str]] = {}
    for g, sid in zip(t["group"].tolist(), t["sample_id"].tolist(), strict=True):
        rows_per_split.setdefault(owner[g], set()).add(g)  # KeyError = a row without a split
        assert sid.split("/")[0] in ("mitdb", "ptbxl", "chbmit")
    groups_seen = [g for s in rows_per_split.values() for g in s]
    assert len(groups_seen) == len(set(groups_seen))
    assert len(rows_per_split) >= 3 and "train" in rows_per_split and "test" in rows_per_split


def test_mitbih_protocol_and_labels() -> None:
    d, t = BUILDS["mitbih"]()
    assign = d.extra["split"]["assign"]
    assert set(assign["test"]) == {"mitdb_100", "mitdb_103", "mitdb_105"}  # DS2 records only
    dev = {g for k in ("train", "search_val", "confirm_val") for g in assign[k]}
    assert dev == {f"mitdb_{r}" for r in ("101", "106", "108", "109", "112")}
    assert d.extra["label_map"] == {"0": "N", "1": "S", "2": "V", "3": "F", "4": "Q"}
    assert set(t["y"].tolist()) == {0, 1, 2, 3, 4}
    assert t["X"].shape[1] == 90 + 162 and t["rr_pre_s"].min() > 0
    assert d.expected_source_sha256 == d.observed_source_sha256 and d.license == "ODC-By-1.0"
    assert d.extra["source_checksums"]["files_unverified"] == []
    assert d.overlapping_windows is True  # 0.70 s beat windows overlap when RR < 0.70 s (review #9)
    assert d.egress == "cloud_ok"  # ODC-By open access; other licences stay local_only (ADR-159)
    from researchos_plugin_physio_signals.common import egress_for

    assert egress_for("PhysioNet Credentialed Health Data License 1.5.0") == "local_only"


def test_mitbih_record_202_and_paced_records_refused() -> None:
    assert mitbih.subject_of("202") == mitbih.subject_of("201") and "202" not in mitbih.DS2
    for rec in ("202", "102"):
        with pytest.raises(ValueError, match="excluded"):
            mitbih.selected_records({"records": [rec]}, [rec])


def test_mitbih_uses_mlii_by_name() -> None:
    files = mitdb_files(("109",))
    from researchos_plugin_physio_signals import wfdb

    h = wfdb.parse_header(files["109.hea"].decode())
    assert h.signals[0].description == "V1" and mitbih.lead_index(h) == 1


def test_ptbxl_folds_labels_and_exclusions() -> None:
    d, t = BUILDS["ptbxl"]()
    assign = d.extra["split"]["assign"]
    assert set(assign["test"]) == {"ptbxl_p111", "ptbxl_p112"}
    assert set(assign["search_val"]) == {"ptbxl_p109", "ptbxl_p110"} and assign["confirm_val"] == []
    assert d.extra["excluded_records"] == {"multi_superclass": 1, "no_superclass": 1}
    assert t["X"].shape[1:] == (100, 12) and d.extra["leads"][0] == "I" and d.license == "CC-BY-4.0"
    assert t["sample_id"].tolist()[:2] == ["ptbxl/1", "ptbxl/2"] and t["group"].tolist()[:2] == ["ptbxl_p101"] * 2


def test_ptbxl_confirm_fold_and_cap() -> None:
    http = tree_http(PTBXL, ptbxl_files(), license_text=CC_BY)
    d, t = _build(ptbxl.PtbXlProvider(), http, {"confirm_fold": 1, "max_records_per_fold": 1})
    assert d.extra["split"]["assign"]["confirm_val"] == ["ptbxl_p101"] and t["sample_id"].tolist()[0] == "ptbxl/1"
    assert "ptbxl/2" not in t["sample_id"].tolist()


def test_ptbxl_patient_in_two_folds_aborts() -> None:
    rows = (*PTB_ROWS, (16, 101, 10, "{'NORM': 100.0}"))
    with pytest.raises(LeakageError, match="ptbxl_p101"):
        _build(ptbxl.PtbXlProvider(), tree_http(PTBXL, ptbxl_files(rows), license_text=CC_BY), {})


def test_chbmit_subject_merge_windows_and_unverified_checksum() -> None:
    d, t = BUILDS["chbmit"]()
    assert "chbmit_chb21" not in set(t["group"].tolist())
    assert any(s.startswith("chbmit/chb21_01.edf/") for s in t["sample_id"].tolist())
    assert t["X"].shape[1:] == (3, 128) and set(t["y"].tolist()) == {0, 1}
    ictal = [s for s, y in zip(t["sample_id"].tolist(), t["y"].tolist(), strict=True) if y == 1]
    assert {int(s.rsplit("/", 1)[1]) for s in ictal if "chb01_01" in s} == {5, 6}  # 20-28 s at 4 s windows
    assert d.expected_source_sha256 is None and d.extra["source_checksums"]["upstream_checksums"] is None
    assert d.extra["units"] == ["uV"] and d.extra["subset_limited"] is True


def test_chbmit_requires_subjects() -> None:
    with pytest.raises(ValueError, match="subjects"):
        _build(chbmit.ChbMitProvider(), tree_http(CHBMIT, chbmit_files(), sums=False), {})


def test_chbmit_summary_count_mismatch_is_an_error() -> None:
    bad = (
        "File Name: x.edf\nNumber of Seizures in File: 2\nSeizure Start Time: 1 seconds\nSeizure End Time: 2 seconds\n"
    )
    with pytest.raises(chbmit.SummaryError):
        chbmit.parse_summary(bad)


def test_checksum_mismatch_fails_the_build() -> None:
    http = tree_http(MITDB, mitdb_files(), tamper="101.dat")
    with pytest.raises(UpstreamError, match=r"checksum mismatch .*101\.dat"):
        _build(mitbih.MitBihProvider(), http, dict(MIT_SETTINGS))


def test_missing_checksum_list_is_reported_unverified() -> None:
    d, _ = _build(mitbih.MitBihProvider(), tree_http(MITDB, mitdb_files(), sums=False), dict(MIT_SETTINGS))
    assert d.expected_source_sha256 is None and d.observed_source_sha256
    assert "101.dat" in d.extra["source_checksums"]["files_unverified"]


def test_unknown_license_fails_unless_owner_states_it() -> None:
    http = tree_http(MITDB, mitdb_files(), license_text="some text without a known license")
    with pytest.raises(UpstreamError, match="license"):
        _build(mitbih.MitBihProvider(), http, dict(MIT_SETTINGS))
    d, _ = _build(mitbih.MitBihProvider(), http, {**MIT_SETTINGS, "license_override": "ODC-By-1.0"})
    assert d.license == "ODC-By-1.0" and d.extra["license_source"] == "owner setting license_override"
    assert detect_license(CC_BY) == "CC-BY-4.0" and detect_license("") is None


def test_cancel_stops_the_build() -> None:
    with tempfile.TemporaryDirectory() as td:
        ctx = FakeBuildContext(Path(td), tree_http(MITDB, mitdb_files()), cancelled=True)
        with pytest.raises(BuildCancelled):
            mitbih.MitBihProvider().build(ctx, dict(MIT_SETTINGS))


def test_only_physionet_is_contacted() -> None:
    http = tree_http(MITDB, mitdb_files())
    _build(mitbih.MitBihProvider(), http, dict(MIT_SETTINGS))
    assert http.calls and all(c.startswith("https://physionet.org/files/mitdb/1.0.0/") for c in http.calls)
    with pytest.raises(UpstreamError):
        fetch_bytes(http, "https://example.org/x")


class RangeHttp(FakeHttp):
    """FakeHttp that honours Range headers (206 + Content-Range), like physionet.org."""

    def __init__(self, url: str, data: bytes) -> None:
        super().__init__({}, ("physionet.org",))
        self.url, self.data = url, data
        self.ranges: list[tuple[int, int]] = []

    def get(self, url: str, *, params: Any = None, headers: Any = None) -> HttpResponse:
        assert url == self.url
        a, b = (int(x) for x in headers["Range"].removeprefix("bytes=").split("-"))
        self.ranges.append((a, b))
        part = self.data[a : b + 1]
        return HttpResponse(206, {"Content-Range": f"bytes {a}-{a + len(part) - 1}/{len(self.data)}"}, part, url)


def test_ranged_download_reassembles_chunks() -> None:
    data = bytes(range(256)) * 41
    http = RangeHttp("https://physionet.org/files/x/1/f.edf", data)
    assert fetch_bytes(http, http.url, chunk=1000) == data and len(http.ranges) == 11


def test_seeded_partition_is_deterministic_and_disjoint() -> None:
    groups = [f"g{i}" for i in range(10)]
    a = seeded_partition(groups, {"test": 0.2, "search_val": 0.2}, 3)
    assert a == seeded_partition(groups, {"test": 0.2, "search_val": 0.2}, 3)
    assert sorted(g for v in a.values() for g in v) == sorted(groups) and len(a["test"]) == 2
    with pytest.raises(LeakageError):
        split_spec({"train": ["g1"], "test": ["g1"]}, ["g1"])
    with pytest.raises(LeakageError, match="without a split"):
        split_spec({"train": ["g1"]}, ["g1", "g2"])


def test_mitbih_segments_carry_reference_peaks_and_rate() -> None:
    """12 s synthetic records, beats every 288 samples from 150: one 10 s segment each, 12 reference peaks."""
    d, t = _build(
        mitbih.MitBihProvider(), tree_http(MITDB, mitdb_files()), {**MIT_SETTINGS, "unit": "segment", "segment_s": 10}
    )
    assert t["X"].shape == (8, 3600) and t["r_peaks"].shape == (8, 40) and t["r_peaks"].dtype == np.int64
    peaks = t["r_peaks"][0]
    assert peaks[peaks >= 0].tolist() == list(range(150, 3600, 288)) and (peaks[12:] == -1).all()
    assert t["sampling_rate_hz"].tolist() == [360.0] * 8 and t["y"].tolist() == [1] * 8  # V beat in every segment
    assert t["sample_id"][0] == "mitdb/100/0-3600"
    assert d.extra["unit"] == "segment" and d.extra["label_map"] == {"0": "no_SVF_beat", "1": "any_SVF_beat"}
    assert d.extra["dropped_segments"] == {"dropped_tail_samples": 8 * 720, "dropped_invalid": 0}
    assert d.overlapping_windows is False


def _segments(anns: list[wfdb.Annotation], signal: np.ndarray[Any, Any]) -> Table:
    header = wfdb.Header("100", 1, 10.0, len(signal), (), ())
    table, stats = Table(), {"dropped_tail_samples": 0, "dropped_invalid": 0}
    mitbih.extract_segments("100", header, signal, anns, table, stats, 10.0)
    return table


def test_mitbih_beat_symbols_match_wfdb_isqrs() -> None:
    # Official default isqrs mapping: https://physionet.org/files/wfdb/10.7.0/lib/ecgmap.h
    # Codes 1-13, 25, 30, 31, 34, 35, 38, 41, independently of AAMI_MAP.
    expected = {"N", "L", "R", "a", "V", "F", "J", "A", "S", "E", "j", "/", "Q", "B", "?", "!", "e", "n", "f", "r"}
    assert mitbih.BEAT_SYMBOLS == expected


def test_mitbih_flutter_wave_is_a_reference_peak() -> None:
    table = _segments([wfdb.Annotation(5, 1, "N"), wfdb.Annotation(20, 31, "!")], np.zeros(100))
    assert table.extra["r_peaks"][0][:3].tolist() == [5, 20, -1]
    assert table.y == [0]


def test_mitbih_segment_labels_skip_invalid_and_refuse_overfull() -> None:
    beat = wfdb.Annotation
    signal = np.zeros(300)
    signal[150] = np.nan
    anns = [beat(5, 1, "N"), beat(50, 1, "Q"), beat(60, 1, "+"), beat(120, 1, "A"), beat(250, 1, "V")]
    table = _segments(anns, signal)
    assert table.y == [0, 1] and table.sample_id == ["mitdb/100/0-100", "mitdb/100/200-300"]
    assert table.extra["r_peaks"][0][:3].tolist() == [5, 50, -1]  # Q is a beat; '+' is a rhythm note
    with pytest.raises(wfdb.WfdbError, match="exceed"):
        _segments([beat(i, 1, "N") for i in range(41)], np.zeros(100))
