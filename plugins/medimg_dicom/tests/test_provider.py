"""medimg_dicom_folder on pydicom's bundled test files (MIT), copied into a temporary owner root. Offline."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pydicom
import pytest
from pydicom.data import get_testdata_file
from researchos.sdk.conformance import DatasetProviderConformance
from researchos.sdk.testing import FakeBuildContext, FakeHttp, FakeLocalRead

from researchos_plugin_medimg_dicom import images
from researchos_plugin_medimg_dicom.provider import DicomBuildError, DicomFolderProvider

FILES = ("CT_small.dcm", "MR_small.dcm", "MR_small_RLE.dcm", "MR_small_bigendian.dcm", "JPEG2000.dcm")
HEADER_VALUES = ("CompressedSamples", "1CT1", "4MR1", "1.3.6.1.4.1.5962")  # names, IDs and UID roots in the files
SETTINGS = {"license": "pydicom test files (MIT)", "phi_handling": "pseudonymize", "resize_to": [32, 32]}


def make_root(tmp: Path, edit: Any = None) -> Path:
    root = tmp / "scans"
    root.mkdir(parents=True)
    for name in FILES:
        shutil.copy(get_testdata_file(name), root / name)
    (root / "notes.txt").write_text("not dicom")
    if edit:
        edit(root)
    return root


def build(root: Path, out: Path, **settings: Any) -> tuple[Any, dict[str, Any]]:
    ctx = FakeBuildContext(out, FakeHttp({}, ()), local=FakeLocalRead(root))
    d = DicomFolderProvider().build(ctx, {"directory": str(root), **SETTINGS, **settings})
    with np.load(out / d.table, allow_pickle=False) as t:
        return d, {k: t[k] for k in t.files}


class TestConformance(DatasetProviderConformance):
    def setup_method(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        self.root = make_root(self._tmp)

    def teardown_method(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def make_provider(self) -> DicomFolderProvider:
        return DicomFolderProvider()

    def make_http(self) -> FakeHttp:
        return FakeHttp({}, ())

    def make_local(self) -> FakeLocalRead:
        return FakeLocalRead(self.root)

    def settings(self) -> dict[str, Any]:
        return {"directory": str(self.root), **SETTINGS}


def test_default_refuses_and_reports_keywords_not_values(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    with pytest.raises(DicomBuildError) as e:
        build(root, tmp_path, phi_handling="refuse")
    msg = str(e.value)
    assert "PatientName" in msg and "PatientID" in msg and "pseudonymize" in msg
    assert not any(v in msg for v in HEADER_VALUES)


def test_pseudonymized_table_holds_no_header_values(tmp_path: Path) -> None:
    d, t = build(make_root(tmp_path), tmp_path)
    assert d.egress == "local_only"
    assert t["X"].shape == (4, 32, 32) and d.extra["skipped"] == {"compressed_syntax_not_decoded": 1, "not_dicom": 1}
    assert d.extra["label_map"] == {"0": "CT", "1": "MR"} and sorted(t["y"].tolist()) == [0, 1, 1, 1]
    blob = (tmp_path / d.table).read_bytes() + json.dumps(dict(d.extra)).encode() + d.source.encode()
    assert not any(v.encode() in blob for v in HEADER_VALUES)
    groups = dict(zip(t["y"].tolist(), t["group"].tolist(), strict=False))
    assert len(set(t["group"][t["y"] == 1].tolist())) == 1 and groups[0] != groups[1]  # one MR patient, one CT
    (tmp_path / "second").mkdir(exist_ok=True)
    _, again = build(make_root(tmp_path / "second"), tmp_path / "second")
    assert set(again["group"].tolist()).isdisjoint(t["group"].tolist())  # salt discarded: builds are unlinkable


def test_ct_rescale_resize_spacing_and_units(tmp_path: Path) -> None:
    d, t = build(make_root(tmp_path), tmp_path)
    ds = pydicom.dcmread(get_testdata_file("CT_small.dcm"))
    expected = ds.pixel_array.astype(float) * float(ds.RescaleSlope) + float(ds.RescaleIntercept)
    rows = ((np.arange(32) + 0.5) * 128 / 32).astype(int)  # nearest pixel centres, independent of images.resize
    ct = int(np.flatnonzero(t["y"] == 0)[0])
    assert np.allclose(t["X"][ct], expected[np.ix_(rows, rows)])
    assert t["pixel_spacing_mm"][ct] == pytest.approx([0.661468 * 4] * 2)
    assert images.units(ds) == "HU" and "HU" in d.extra["modality_units"]


@pytest.mark.parametrize(
    "edit,setting,message",
    [
        (lambda r: _set(r / "CT_small.dcm", BurnedInAnnotation="YES"), {}, "burned-in"),
        (lambda r: _set(r / "CT_small.dcm", PatientID=""), {}, "PatientID missing"),
        (None, {"resize_to": None}, "different shapes"),
    ],
)
def test_refusals(tmp_path: Path, edit: Any, setting: dict[str, Any], message: str) -> None:
    root = make_root(tmp_path, edit)
    extra = {k: v for k, v in setting.items() if v is not None}
    ctx = FakeBuildContext(tmp_path, FakeHttp({}, ()), local=FakeLocalRead(root))
    settings = {"directory": str(root), **SETTINGS, **extra}
    if "resize_to" in setting:
        settings.pop("resize_to")
    with pytest.raises(DicomBuildError, match=message):
        DicomFolderProvider().build(ctx, settings)


def test_without_local_access_the_build_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DicomBuildError, match="files:read"):
        DicomFolderProvider().build(FakeBuildContext(tmp_path, FakeHttp({}, ())), {"directory": "/x", **SETTINGS})


def _set(path: Path, **values: str) -> None:
    ds = pydicom.dcmread(path)
    for k, v in values.items():
        setattr(ds, k, v)
    ds.save_as(path)


def _poison(root: Path) -> None:
    for name in ("CT_small.dcm", "MR_small.dcm"):
        _set(root / name, BodyPartExamined=SENTINEL, RescaleType=SENTINEL)
    _set(root / "MR_small.dcm", Modality=SENTINEL)


SENTINEL = "SENTINEL_X9"


@pytest.mark.parametrize("label_attribute", ["Modality", "BodyPartExamined"])
def test_header_free_text_never_reaches_table_manifest_or_errors(tmp_path: Path, label_attribute: str) -> None:
    """Regression (review #42): BodyPartExamined / RescaleType / Modality values reached labels, units and manifest."""
    extra = {"label_values": ["CHEST"]} if label_attribute == "BodyPartExamined" else {}
    d, t = build(make_root(tmp_path, _poison), tmp_path, label_attribute=label_attribute, **extra)
    blob = (tmp_path / d.table).read_bytes() + json.dumps(dict(d.extra)).encode()
    assert SENTINEL.encode() not in blob and "other" in d.extra["label_map"].values()
    with pytest.raises(DicomBuildError) as e:  # the refusal summary is also value-free
        build(make_root(tmp_path / "again", _poison), tmp_path / "again", phi_handling="refuse")
    assert SENTINEL not in str(e.value)


def test_body_part_labels_need_an_owner_list(tmp_path: Path) -> None:
    with pytest.raises(DicomBuildError, match="label_values"):
        build(make_root(tmp_path), tmp_path, label_attribute="BodyPartExamined")


def test_unknown_burned_in_on_risky_modality_needs_owner_check(tmp_path: Path) -> None:
    """Regression (review #44): absent BurnedInAnnotation on US now refuses until the owner accepts it."""
    root = make_root(tmp_path, lambda r: _set(r / "CT_small.dcm", Modality="US"))
    with pytest.raises(DicomBuildError, match="accept_burned_in_unknown"):
        build(root, tmp_path)
    d, _ = build(root, tmp_path, accept_burned_in_unknown=True)
    assert d.extra["phi_preflight"]["burned_in_unknown_risky_modality"] == 1


NAMED = "DOE^JANE_1970"  # a folder named after a patient: must never appear in errors


def test_errors_never_carry_paths_and_bad_files_are_counted(tmp_path: Path) -> None:
    """Containment review (ADR-166): oversize files, a total cap and truncated DICOM, under a name-bearing path."""
    root = tmp_path / NAMED
    root.mkdir()
    for name in FILES:
        shutil.copy(get_testdata_file(name), root / name)
    data = (root / "CT_small.dcm").read_bytes()
    (root / "truncated.dcm").write_bytes(data[: len(data) // 2])
    ctx = FakeBuildContext(tmp_path / "out", FakeHttp({}, ()), local=FakeLocalRead(tmp_path))
    (tmp_path / "out").mkdir()
    base = {"directory": str(root), **SETTINGS}
    d = DicomFolderProvider().build(ctx, base)
    assert d.extra["skipped"].get("unreadable_dicom", 0) + d.extra["skipped"].get("undecodable_pixels", 0) == 1
    assert NAMED not in json.dumps(dict(d.extra)) + d.source
    for settings, message in (
        ({"max_file_mb": 1, "directory": str(root)}, None),
        ({"max_total_mb": 1}, "max_total_mb"),
        ({"directory": str(tmp_path / "missing" / NAMED)}, "cannot list"),
    ):
        big = root / "big.dcm"
        if "max_file_mb" in settings or "max_total_mb" in settings:
            big.write_bytes(b"\0" * 1_100_000)
        with pytest.raises(DicomBuildError) as e:
            DicomFolderProvider().build(ctx, {**base, **settings})
        assert NAMED not in str(e.value) and (message is None or message in str(e.value))
        big.unlink(missing_ok=True)


def _deflated(path: Path, rows: int = 512, instance: str | None = None) -> None:
    """Codex repro #63/#64: a tiny deflated file that decodes to rows x rows 16-bit pixels."""
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import DeflatedExplicitVRLittleEndian, generate_uid

    meta = FileMetaDataset()
    meta.TransferSyntaxUID = DeflatedExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID = generate_uid(), generate_uid()
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.PatientID, ds.Modality, ds.BurnedInAnnotation = "synthetic", "MR", "NO"
    ds.Rows = ds.Columns = rows
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
    ds.PixelData = np.zeros((rows, rows), dtype=np.uint16).tobytes()
    if instance is not None:
        ds.add_new(0x00200013, "LO", instance)  # malformed: free text where an integer string belongs
    ds.save_as(path, enforce_file_format=True)


def test_63_malformed_metadata_is_skipped_without_echoing_it(tmp_path: Path) -> None:
    root = tmp_path / "in"
    root.mkdir()
    _deflated(root / "bad.dcm", 16, instance="/patients/DOE_JANE/private.dcm")
    _deflated(root / "good.dcm", 16)
    ctx = FakeBuildContext(tmp_path, FakeHttp({}, ()), local=FakeLocalRead(root))
    d = DicomFolderProvider().build(ctx, {"directory": str(root), **SETTINGS, "resize_to": [16, 16]})
    assert d.extra["skipped"]["malformed_metadata"] == 1 and d.extra["n_samples"] == 1
    assert "DOE_JANE" not in json.dumps(dict(d.extra))
    (root / "good.dcm").unlink()
    with pytest.raises(DicomBuildError) as e:
        DicomFolderProvider().build(ctx, {"directory": str(root), **SETTINGS})
    assert "DOE_JANE" not in str(e.value)


def test_64_decoded_memory_is_bounded_not_only_source_bytes(tmp_path: Path) -> None:
    root = tmp_path / "in"
    root.mkdir()
    _deflated(root / "tiny.dcm", 512)
    assert (root / "tiny.dcm").stat().st_size < 10_000  # ~2 MB of float64 pixels from a few KB
    ctx = FakeBuildContext(tmp_path, FakeHttp({}, ()), local=FakeLocalRead(root))
    with pytest.raises(DicomBuildError, match="max_decoded_mb"):
        DicomFolderProvider().build(ctx, {"directory": str(root), **SETTINGS, "max_decoded_mb": 1})
    d = DicomFolderProvider().build(ctx, {"directory": str(root), **SETTINGS, "max_decoded_mb": 4})
    assert d.extra["n_samples"] == 1
