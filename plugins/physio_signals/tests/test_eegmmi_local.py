"""physio_eegmmi from an owner's local copy (ADR-189): read-only ctx.local, explicit licence, honest checksums.
The real-data test runs only where EEGMMIDB_DATA_DIR points to a local subset; it is never committed."""

from __future__ import annotations

import hashlib
import tempfile
import os
from pathlib import Path

import numpy as np
import pytest
from researchos.sdk.testing import FakeBuildContext, FakeHttp, FakeLocalRead

from researchos_plugin_physio_signals import eegmmi
from researchos_plugin_physio_signals.fetch import UpstreamError
from synth import eegmmi_files

REAL = Path(os.environ.get("EEGMMIDB_DATA_DIR", "eegmmidb-not-configured"))


def _copy(root: Path, subjects: tuple[str, ...], sums: bool) -> Path:
    files = eegmmi_files(subjects, slow=())
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    if sums:
        (root / "SHA256SUMS.txt").write_text(
            "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in files.items())
        )
    return root


def _ctx(out: Path, root: Path, local: bool = True) -> FakeBuildContext:
    return FakeBuildContext(out, FakeHttp({}, ()), local=FakeLocalRead(root) if local else None)


SETTINGS = {
    "subjects": ["S001", "S002", "S003"],
    "val_fraction": 0.34,
    "test_fraction": 0.34,
    "license_override": "ODC-By-1.0",
}


def test_local_copy_builds_with_verified_checksums_and_no_network(tmp_path: Path) -> None:
    root = _copy(tmp_path / "copy", ("S001", "S002", "S003"), sums=True)
    (tmp_path / "out").mkdir()
    ctx = _ctx(tmp_path / "out", root)
    d = eegmmi.EegMmiProvider().build(ctx, {**SETTINGS, "local_dir": str(root)})
    assert ctx.http.calls == []  # nothing downloaded
    assert d.source.startswith(str(root)) and d.license == "ODC-By-1.0" and d.egress == "cloud_ok"
    assert d.extra["source_checksums"]["files_verified"] == 9 and d.expected_source_sha256
    assign = d.extra["split"]["assign"]
    assert all(len(assign[k]) == 1 for k in ("train", "search_val", "test"))  # one subject each, disjoint


def test_local_copy_without_checksums_is_unverified_and_needs_a_licence(tmp_path: Path) -> None:
    root = _copy(tmp_path / "copy", ("S001", "S002", "S003"), sums=False)
    (tmp_path / "out").mkdir()
    no_lic = {k: v for k, v in SETTINGS.items() if k != "license_override"}
    with pytest.raises(UpstreamError, match="license_override"):
        eegmmi.EegMmiProvider().build(_ctx(tmp_path / "out", root), {**no_lic, "local_dir": str(root)})
    d = eegmmi.EegMmiProvider().build(_ctx(tmp_path / "out", root), {**SETTINGS, "local_dir": str(root)})
    assert d.expected_source_sha256 is None and len(d.extra["source_checksums"]["files_unverified"]) == 9


def test_local_copy_needs_a_granted_root(tmp_path: Path) -> None:
    root = _copy(tmp_path / "copy", ("S001", "S002", "S003"), sums=False)
    (tmp_path / "out").mkdir()
    with pytest.raises(UpstreamError, match="files:read"):
        eegmmi.EegMmiProvider().build(_ctx(tmp_path / "out", root, local=False), {**SETTINGS, "local_dir": str(root)})
    with pytest.raises(PermissionError):  # a directory outside the granted root is refused by the host
        eegmmi.EegMmiProvider().build(_ctx(tmp_path / "out", root), {**SETTINGS, "local_dir": str(tmp_path)})


@pytest.mark.skipif(not REAL.is_dir(), reason="owner's local EEGMMIDB subset not present")
def test_real_local_subset() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = eegmmi.EegMmiProvider().build(_ctx(Path(td), REAL), {**SETTINGS, "local_dir": str(REAL)})
        with np.load(Path(td) / d.table, allow_pickle=False) as t:
            x, y, groups, fs = t["X"], t["y"], t["group"], t["sampling_rate_hz"]
    assert d.extra["skipped_records"] == {} and d.extra["sampling_rate_hz"] == 160
    assert x.shape[1:] == (3, 640) and set(np.unique(fs)) == {160.0}  # C3, Cz, C4 x 4 s at 160 Hz
    assert set(groups) == {"S001", "S002", "S003"} and set(y.tolist()) == {0, 1}
    assert d.extra["source_checksums"]["files_unverified"]  # the subset ships no SHA256SUMS.txt: said so


def test_copy_without_local_sums_is_verified_against_physionet(tmp_path: Path) -> None:
    from synth import EEGMMI, tree_http

    root = _copy(tmp_path / "copy", ("S001", "S002", "S003"), sums=False)
    (tmp_path / "out").mkdir()
    http = tree_http(EEGMMI, eegmmi_files(("S001", "S002", "S003"), slow=()))
    ctx = FakeBuildContext(tmp_path / "out", http, local=FakeLocalRead(root))
    d = eegmmi.EegMmiProvider().build(ctx, {**SETTINGS, "local_dir": str(root)})
    assert http.calls == [EEGMMI + "SHA256SUMS.txt"]  # only the public checksum list, no data download
    assert d.extra["source_checksums"]["files_verified"] == 9 and d.expected_source_sha256
    assert d.extra["source_checksums"]["upstream_checksums"] == EEGMMI + "SHA256SUMS.txt"
    (root / "S002" / "S002R08.edf").write_bytes((root / "S002" / "S002R08.edf").read_bytes() + b"\0")
    (tmp_path / "out2").mkdir()
    with pytest.raises(UpstreamError, match="checksum mismatch"):
        eegmmi.EegMmiProvider().build(
            FakeBuildContext(tmp_path / "out2", http, local=FakeLocalRead(root)), {**SETTINGS, "local_dir": str(root)}
        )
