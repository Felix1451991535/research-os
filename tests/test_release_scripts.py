"""Release scripts (ADR-225): manifest builder, publish dry run, version bump rewrite and the local update e2e check
with a small fake installer (offline, no git tag, no gh call)."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import researchos
from researchos.kernel.config import Settings
from researchos.services.ops.updater import asset_name, parse_release

ROOT = Path(__file__).resolve().parents[1]
REL = ROOT / "scripts" / "release"
V = researchos.__version__


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(REL))
    spec = importlib.util.spec_from_file_location(name, REL / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


common, manifest, publish, bump, e2e = (_load(n) for n in ("_common", "manifest", "publish", "bump_version",
                                                           "e2e_check"))


@pytest.fixture()
def exe(tmp_path: Path) -> Path:
    p = tmp_path / asset_name(V)
    p.write_bytes(b"MZ fake installer for the release tests")
    return p


def test_repo_and_download_url_come_from_the_default_feed() -> None:
    owner_repo = common.github_repo()
    assert Settings.update_feed_url == f"https://github.com/{owner_repo}/releases/latest/download/latest.json"
    assert common.download_url("9.9.9") == (f"https://github.com/{owner_repo}/releases/download/v9.9.9/"
                                            "ResearchOS-Setup-9.9.9.exe")
    with pytest.raises(ValueError):
        common.github_repo("https://example.org/a/b/latest.json")


def test_manifest_writes_sha256_and_a_feed_the_updater_accepts(exe: Path) -> None:
    assert manifest.main(["--exe", str(exe), "--notes", "fixes"]) == 0
    rel = parse_release((exe.parent / "latest.json").read_bytes(), Settings.update_feed_url)
    digest = rel.sha256
    assert (rel.version, rel.asset, rel.notes, rel.url) == (V, exe.name, "fixes", common.download_url(V))
    assert (exe.parent / f"{exe.name}.sha256").read_text(encoding="utf-8") == f"{digest}  {exe.name}\n"


def test_manifest_refuses_an_exe_of_another_version(tmp_path: Path) -> None:
    other = tmp_path / asset_name("0.0.1")
    other.write_bytes(b"x")
    assert manifest.main(["--exe", str(other), "--notes", "n"]) == 1
    assert not (tmp_path / "latest.json").exists()


def test_publish_is_a_dry_run_without_yes(exe: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    manifest.main(["--exe", str(exe), "--notes-file", str(_notes(exe.parent))])
    monkeypatch.setattr(publish, "git_problems", lambda tag: [])

    def forbidden(*a: object, **k: object) -> None:
        raise AssertionError("dry run executed a command")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert publish.main(["--dir", str(exe.parent)]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and f"gh release create v{V} --repo {common.github_repo()}" in out
    assert "--notes 'line one'" in out and str(exe.parent / "latest.json") in out


def test_publish_preflight_catches_a_stale_sha256_file(exe: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest.main(["--exe", str(exe), "--notes", "n"])
    exe.write_bytes(b"rebuilt after the manifest")
    problems = publish.file_problems(publish.files(exe.parent, V), V)
    assert any("sha256 does not match" in p for p in problems) and len(problems) == 2


def test_bump_rewrites_every_derived_copy(tmp_path: Path) -> None:
    for name in bump.REWRITES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, tmp_path / name)
    bump.rewrite(tmp_path, "98.7.6")
    assert bump.current(tmp_path) == "98.7.6"
    assert '"version": "98.7.6"' in (tmp_path / "apps/web/package.json").read_text(encoding="utf-8")
    assert '"version": "98.7.6"' in (tmp_path / "packages/contracts/openapi.json").read_text(encoding="utf-8")
    assert 'name = "researchos"\nversion = "98.7.6"' in (tmp_path / "uv.lock").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        bump.check_new("1.2.3", "1.2.3")


def test_local_update_e2e_with_a_fake_installer(tmp_path: Path) -> None:
    steps = {s.name: s.status for s in e2e.run(None, tmp_path)}
    assert steps == {
        "good: check -> available": "PASS",
        "good: download -> sha256 verified -> ready": "PASS",
        "tampered: check -> available": "PASS",
        "tampered: download -> update_checksum_mismatch": "PASS",
        "tampered: installer deleted": "PASS",
        "install (silent setup hand-off)": "NOT RUN",
    }


def test_e2e_reports_fail_when_the_served_installer_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = e2e.good_run
    monkeypatch.setattr(e2e, "good_run", lambda up, digest: real(up, "0" * 64))
    assert not e2e.report(e2e.run(None, tmp_path))


def _notes(d: Path) -> Path:
    p = d / "notes.md"
    p.write_text("line one\n", encoding="utf-8")
    return p
