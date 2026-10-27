"""core 1.5 LocalReadContext (ADR-155): host reads obey owner file roots; the SDK fake obeys its test root."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from researchos.kernel.config import Settings
from researchos.sdk.testing import FakeLocalRead
from researchos.services.effects import roots
from researchos.services.effects.local_read import RootsReader


@pytest.fixture
def tree(tmp_path: Path, conn: sqlite3.Connection, settings: Settings) -> Path:
    root = tmp_path / "scans"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "one.DCM").write_bytes(b"x" * 10)
    (root / "a" / "b" / "two.dcm").write_bytes(b"y" * 20)
    (root / "notes.txt").write_text("n")
    outside = tmp_path / "secret.dcm"
    outside.write_bytes(b"s")
    (root / "link.dcm").symlink_to(outside)
    roots.set_roots(conn, settings.data_dir, [{"path": str(root), "access": "read"}], "owner")
    return root


def test_reads_inside_read_roots_only(tree: Path, settings: Settings) -> None:
    r = RootsReader(settings.db_path)
    files = r.list_files(str(tree), suffixes=(".dcm",))
    assert files == [str(tree / "a" / "b" / "two.dcm"), str(tree / "a" / "one.DCM")]  # symlink skipped
    assert r.read_bytes(files[0], max_bytes=100) == b"y" * 20
    with pytest.raises(PermissionError):
        r.read_bytes(str(tree.parent / "secret.dcm"), max_bytes=100)
    with pytest.raises(PermissionError):
        r.read_bytes(str(tree / "link.dcm"), max_bytes=100)  # crosses a symlink out of the root
    with pytest.raises(FileNotFoundError):
        r.read_bytes(str(tree / "missing.dcm"), max_bytes=100)
    with pytest.raises(ValueError, match="larger"):
        r.read_bytes(files[0], max_bytes=5)
    with pytest.raises(ValueError, match="more than 1"):
        r.list_files(str(tree), limit=1)


def test_no_roots_means_no_reads(tmp_path: Path, conn: sqlite3.Connection, settings: Settings) -> None:
    (tmp_path / "f.dcm").write_bytes(b"x")
    with pytest.raises(PermissionError):
        RootsReader(settings.db_path).list_files(str(tmp_path))


def test_sdk_fake_matches_the_contract(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.dcm").write_bytes(b"1")
    fake = FakeLocalRead(tmp_path / "d")
    assert fake.list_files(str(tmp_path / "d"), suffixes=(".DCM",)) == [str(tmp_path / "d" / "x.dcm")]
    with pytest.raises(PermissionError):
        fake.list_files(str(tmp_path))


def test_capability_is_read_from_the_manifest() -> None:
    from researchos.contracts.plugins import PluginManifest
    from researchos.services.extensions import Extensions

    ext = Extensions()
    ext.owners["datasources:a"] = PluginManifest(name="a_plugin", version="1", core_contract=">=1.5,<2",
                                                 capabilities=["files:read"])
    ext.owners["datasources:b"] = PluginManifest(name="b_plugin", version="1", core_contract=">=1.5,<2")
    assert ext.has_capability("datasources:a", "files:read") and not ext.has_capability("datasources:b", "files:read")
    assert not ext.has_capability("datasources:missing", "files:read")


# ---- containment review (ADR-163) ----


def test_dotdot_and_absolute_escapes_are_refused(tree: Path, settings: Settings) -> None:
    r = RootsReader(settings.db_path)
    for raw in (str(tree / "a" / ".." / ".." / "secret.dcm"), "/etc/hostname", "relative/one.DCM"):
        with pytest.raises(PermissionError):
            r.read_bytes(raw, max_bytes=100)


def test_hard_links_are_refused(tree: Path, settings: Settings) -> None:
    os.link(tree.parent / "secret.dcm", tree / "hard.dcm")  # a name inside the root for a file outside it
    with pytest.raises(PermissionError, match="hard links"):
        RootsReader(settings.db_path).read_bytes(str(tree / "hard.dcm"), max_bytes=100)


@pytest.mark.skipif(os.name == "nt", reason="POSIX openat path; Windows re-checks the opened handle")
def test_component_swapped_after_the_check_is_not_followed(tree: Path, settings: Settings,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """TOCTOU: the path is checked, then a directory on it becomes a symlink to outside before the open."""
    outside = tree.parent / "outside"
    outside.mkdir()
    (outside / "one.DCM").write_bytes(b"stolen")
    r = RootsReader(settings.db_path)
    checked = r._resolve(str(tree / "a" / "one.DCM"))
    (tree / "a").rename(tree / "a_moved")
    (tree / "a").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(r, "_resolve", lambda raw: checked)
    with pytest.raises(PermissionError, match="changed while being opened"):
        r.read_bytes(str(tree / "a" / "one.DCM"), max_bytes=100)


def test_listing_does_not_enter_linked_directories(tree: Path, settings: Settings) -> None:
    outside = tree.parent / "elsewhere"
    outside.mkdir()
    (outside / "x.dcm").write_bytes(b"x")
    (tree / "linked").symlink_to(outside, target_is_directory=True)
    files = RootsReader(settings.db_path).list_files(str(tree))
    assert not any("x.dcm" in f for f in files)


def test_a_root_cannot_alias_a_protected_location(tmp_path: Path, conn: sqlite3.Connection, settings: Settings) -> None:
    from researchos.contracts.errors import ApiError

    (tmp_path / "innocent").symlink_to("/etc", target_is_directory=True)
    with pytest.raises(ApiError, match="protected"):
        roots.set_roots(conn, settings.data_dir, [{"path": str(tmp_path / "innocent"), "access": "read"}], "owner")
