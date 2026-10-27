"""Data integrity, boundary safety, split leakage, cancellation and metric checks."""

import csv
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from researchos.sdk.testing import FakeBuildContext
from test_har_datasets_conformance import TestCNN as CNNCase
from test_har_datasets_conformance import TestRF as RFCase
from test_har_datasets_conformance import TestUci as UciCase
from test_har_datasets_conformance import TestWisdm as WisdmCase

from researchos_plugin_har_datasets.archive import read_archive
from researchos_plugin_har_datasets.parsers import subject_split, wisdm, wisdm_with_stats


@pytest.mark.parametrize("case", [UciCase(), WisdmCase()])
def test_checksum_mismatch(case: UciCase, tmp_path: Path) -> None:
    settings = {**case.settings(), "source_sha256": "0" * 64}
    with pytest.raises(ValueError, match="checksum"):
        case.make_provider().build(FakeBuildContext(tmp_path, case.make_http()), settings)
    assert not list(tmp_path.iterdir())


def test_unverified_checksum_is_explicit(tmp_path: Path) -> None:
    case = UciCase()
    settings = {**case.settings(), "source_sha256": None}
    result = case.make_provider().build(FakeBuildContext(tmp_path, case.make_http()), settings)
    assert result.expected_source_sha256 is None
    assert result.extra["checksum_status"] == "upstream checksum unverified"
    assert "official_test_subjects" in result.extra or "skipped_malformed_records" in result.extra


def test_cancelled_build_no_files(tmp_path: Path) -> None:
    case = UciCase()
    with pytest.raises(InterruptedError):
        case.make_provider().build(FakeBuildContext(tmp_path, case.make_http(), cancelled=True), case.settings())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/windows", "x\\escape"])
def test_unsafe_archive(name: str) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, "unsafe")
    with pytest.raises(ValueError):
        read_archive(buf.getvalue())


def test_expansion_limit() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("safe", "x" * 10000)
    with pytest.raises(ValueError):
        read_archive(buf.getvalue(), 100)


def test_split_order_invariance() -> None:
    groups = np.array(["6", "2", "1", "3", "4", "5"])
    splits = subject_split(groups, 7)
    assert splits == subject_split(groups[::-1], 7)
    a, b, c = (set(splits[k]) for k in ("train", "search_val", "test"))
    assert a and b and c and not (a & b or a & c or b & c)


def test_wisdm_discontinuities() -> None:
    # Two sub-window segments separated by a timestamp gap must not be joined.
    raw = b"1,Walking,1000000000,1,2,3;\n1,Walking,1050000000,1,2,3;\n1,Walking,5000000000,1,2,3;"
    with pytest.raises(ValueError):
        wisdm({"WISDM_ar_v1.1_raw.txt": raw}, "synthetic", 3, 150000000)


@pytest.mark.parametrize("case", [RFCase(), CNNCase()])
def test_recomputed_metrics_and_cancellation(case: RFCase, tmp_path: Path) -> None:
    case._prepare(tmp_path)
    assert case._run(tmp_path) == 0
    matrix = json.loads((tmp_path / "confusion_matrix.json").read_text())
    with (tmp_path / "predictions/search_val.csv").open() as f:
        rows = list(csv.DictReader(f))
    cm = np.zeros((3, 3), dtype=int)
    for row in rows:
        cm[int(row["y_true"]), int(row["y_pred"])] += 1
    assert cm.tolist() == matrix["matrix"] and int(cm.sum()) == len(rows)
    accuracy = float(np.trace(cm) / cm.sum())
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert next(m["value"] for m in summary if m["name"] == "accuracy") == accuracy
    cancel_dir = tmp_path / "cancelled"
    case._prepare(cancel_dir)
    (cancel_dir / "CANCEL").touch()
    assert case._run(cancel_dir) == 0 and not (cancel_dir / "summary.json").exists()


def test_archive_symlink() -> None:
    import stat

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "../target")
    with pytest.raises(ValueError, match="Symlink"):
        read_archive(buf.getvalue())


def test_small_split_rejected() -> None:
    with pytest.raises(ValueError, match="three"):
        subject_split(np.array(["1", "2"]), 0)


@pytest.mark.parametrize("case", [RFCase(), CNNCase()])
def test_template_reproducible(case: RFCase, tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    case._prepare(first)
    case._prepare(second)
    assert case._run(first) == case._run(second) == 0
    assert (first / "summary.json").read_bytes() == (second / "summary.json").read_bytes()
    assert (first / "predictions/search_val.csv").read_bytes() == (second / "predictions/search_val.csv").read_bytes()


@pytest.mark.parametrize("case", [RFCase(), CNNCase()])
def test_template_single_class(case: RFCase, tmp_path: Path) -> None:
    case._prepare(tmp_path)
    for split in ("train", "search_val"):
        path = tmp_path / "inputs" / (split + ".npz")
        with np.load(path) as f:
            data = dict(f)
        data["y"] = np.zeros(len(data["y"]), dtype=int)
        np.savez(path, **data)
    assert case._run(tmp_path) == 0
    assert json.loads((tmp_path / "confusion_matrix.json").read_text())["labels"] == [0, 1, 2]


@pytest.mark.parametrize("case", [RFCase(), CNNCase()])
def test_template_rejects_subject_leakage(case: RFCase, tmp_path: Path) -> None:
    case._prepare(tmp_path)
    path = tmp_path / "inputs/search_val.npz"
    with np.load(path) as f:
        data = dict(f)
    data["group"] = np.array(["s1"] * len(data["y"]))
    np.savez(path, **data)
    assert case._run(tmp_path) != 0
    assert not (tmp_path / "summary.json").exists()


def test_wisdm_tolerates_rare_malformed_records_and_counts_them() -> None:
    good = [f"1,Walking,{1000000000 + i * 50000000},1,2,3;" for i in range(200)]
    raw = ("\n".join(good[:100]) + "\n1,Walking,,;\n" + "".join(good[100:])).encode()  # concatenated tail
    arrays, stats = wisdm_with_stats({"WISDM_ar_v1.1_raw.txt": raw}, "synthetic", 10, 150000000)
    assert stats == {"records": 201, "skipped_malformed_records": 1}
    assert len(arrays["y"]) == 20  # 2 segments x 10 windows; none spans the dropped record


def test_wisdm_rejects_mostly_malformed_input() -> None:
    raw = b"1,Walking,1000000000,1,2,3;\nbad;\nbad;\n"
    with pytest.raises(ValueError, match="malformed"):
        wisdm({"WISDM_ar_v1.1_raw.txt": raw}, "synthetic", 3, 150000000)
