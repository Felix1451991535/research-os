"""One-click update (ADR-223/228) against a local fake GitHub release server (no internet): version comparison, signed
feed, sha256 verification, timeout, network error, mirror prefix, install hand-off, and that nothing is checked at
startup. The fake release signs its feed with a test key the updater under test trusts (not the embedded keys)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from researchos.kernel.config import Settings
from researchos.services.ops.update_feed import UpdateError, parse_release, validate_mirror
from researchos.services.ops.update_signing import sign
from researchos.services.ops.updater import Updater, install_dir

from .conftest import Client
from .fake_release import CUR, INSTALLER, KEY, TRUSTED, FakeRelease, bump, closed_port


@pytest.fixture
def rel() -> Iterator[FakeRelease]:
    r = FakeRelease()
    yield r
    r.srv.shutdown()


@pytest.fixture
def upd(settings: Settings, rel: FakeRelease, tmp_path: Path) -> Updater:
    settings.update_feed_url = rel.feed
    settings.update_timeout_s = 2.0
    settings.ensure_dirs()
    return Updater(settings, download_dir=tmp_path / "dl", platform="linux", trusted_keys=TRUSTED)


def _code(fn: Any) -> str:
    with pytest.raises(UpdateError) as e:
        fn()
    return e.value.code


# ---- check: version comparison ----

def test_newer_version_is_available(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(bump(CUR, 1))
    st = upd.check()
    assert st["state"] == "available" and st["current_version"] == CUR
    assert st["latest"]["version"] == bump(CUR, 1) and st["latest"]["notes"] == f"notes for {bump(CUR, 1)}"


def test_same_version_is_up_to_date(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(CUR)
    assert upd.check()["state"] == "up_to_date"
    assert _code(upd.start_download) == "update_not_available"


def test_older_version_is_up_to_date_never_a_downgrade(upd: Updater, rel: FakeRelease) -> None:
    rel.publish("0.0.1")
    assert upd.check()["state"] == "up_to_date"
    assert _code(upd.start_download) == "update_not_available"


# ---- download + verification ----

def test_download_verifies_sha256_and_is_ready(upd: Updater, rel: FakeRelease, tmp_path: Path) -> None:
    v = bump(CUR, 1)
    rel.publish(v)
    upd.check()
    upd.start_download()
    upd.wait(10)
    st = upd.status()
    assert st["state"] == "ready" and st["progress"] == {"received": len(INSTALLER), "total": len(INSTALLER)}
    exe = tmp_path / "dl" / f"ResearchOS-Setup-{v}.exe"
    assert exe.read_bytes() == INSTALLER and not list((tmp_path / "dl").glob("*.part"))
    assert st["installer"] == str(exe)
    # a later check reuses the verified file instead of downloading again
    n = len(rel.hits)
    assert upd.check()["state"] == "ready" and len(rel.hits) == n + 2  # feed + signature only


def test_bad_sha256_deletes_the_download(upd: Updater, rel: FakeRelease, tmp_path: Path) -> None:
    rel.publish(bump(CUR, 1), sha="0" * 64)
    upd.check()
    upd.start_download()
    upd.wait(10)
    st = upd.status()
    assert st["state"] == "error" and st["error"]["code"] == "update_checksum_mismatch"
    assert not any((tmp_path / "dl").iterdir())
    assert _code(upd.install) == "update_not_ready"


@pytest.mark.parametrize(("status", "code"), [(404, "update_asset_missing"), (500, "update_http_error")])
def test_download_http_error_is_reported(upd: Updater, rel: FakeRelease, status: int, code: str) -> None:
    """A feed that names a newer version whose installer is not (yet) downloadable."""
    v = bump(CUR, 1)
    rel.publish(v)
    upd.check()
    rel.routes[rel.asset_path(v)] = (status, b"nope", 0.0)
    upd.start_download()
    upd.wait(10)
    assert upd.status()["error"]["code"] == code


# ---- network failures ----

def test_timeout(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(bump(CUR, 1))
    status, body, _ = rel.routes[rel.feed_path]
    rel.routes[rel.feed_path] = (status, body, 2.0)
    upd.s.update_timeout_s = 0.3
    assert _code(upd.check) == "update_timeout"
    st = upd.status()
    assert st["state"] == "error" and st["error"] == {"code": "update_timeout",
                                                      "message": "the update server did not answer in time"}


def test_network_error(upd: Updater) -> None:
    upd.s.update_feed_url = f"http://127.0.0.1:{closed_port()}/x/releases/latest/download/latest.json"
    assert _code(upd.check) == "update_network"


def test_feed_http_error_and_bad_metadata(upd: Updater, rel: FakeRelease) -> None:
    assert _code(upd.check) == "update_http_error"  # nothing published: 404
    rel.routes[rel.feed_path] = (200, b"{not json", 0.0)
    assert _code(upd.check) == "update_unsigned"  # the signature is checked before the bytes are parsed
    rel.routes[rel.feed_path + ".sig"] = (200, sign(b"{not json", KEY).encode(), 0.0)
    assert _code(upd.check) == "update_bad_metadata"


# ---- mirror prefix ----

def test_mirror_prefix_is_used_for_feed_and_installer(upd: Updater, rel: FakeRelease) -> None:
    """The origin is unreachable; everything goes through the mirror (https://ghfast.top/<url> style)."""
    dead = f"http://127.0.0.1:{closed_port()}"
    v = bump(CUR, 1)
    rel.publish(v, host=dead)
    upd.s.update_feed_url = dead + rel.feed_path
    assert _code(upd.check) == "update_network"  # no mirror configured: a plain network error
    upd.set_mirror_prefix(rel.base + "/mirror/")
    assert upd.check()["state"] == "available"
    upd.start_download()
    upd.wait(10)
    assert upd.status()["state"] == "ready" and upd.status()["mirror_prefix"] == rel.base + "/mirror/"
    assert rel.hits[-1] == f"/mirror/{dead}/owner/research-os/releases/download/v{v}/ResearchOS-Setup-{v}.exe"
    assert json.loads((upd.s.config_dir / "update.json").read_text()) == {"mirror_prefix": rel.base + "/mirror/"}


@pytest.mark.parametrize("bad", ["ghfast.top/", "http://ghfast.top/", "https://ghfast.top", "ftp://x/"])
def test_bad_mirror_prefix_is_refused(bad: str) -> None:
    with pytest.raises(UpdateError) as e:
        validate_mirror(bad)
    assert e.value.code == "update_bad_mirror"


def test_good_mirror_prefixes() -> None:
    assert validate_mirror(" https://ghfast.top/ ") == "https://ghfast.top/"
    assert validate_mirror("") == ""


# ---- metadata validation ----

def _meta(**kw: Any) -> bytes:
    d = {"version": "9.9.9", "asset": "ResearchOS-Setup-9.9.9.exe", "sha256": "a" * 64,
         "url": "https://github.com/o/r/releases/download/v9.9.9/ResearchOS-Setup-9.9.9.exe", "notes": "n"}
    d.update(kw)
    return json.dumps(d).encode()


FEED = "https://github.com/o/r/releases/latest/download/latest.json"


@pytest.mark.parametrize("kw", [
    {"asset": "evil.exe"}, {"sha256": "xyz"}, {"version": "not-a-version"},
    {"url": "https://evil.example/o/r/releases/download/v9.9.9/ResearchOS-Setup-9.9.9.exe"},  # other host
    {"url": "http://github.com/o/r/releases/download/v9.9.9/ResearchOS-Setup-9.9.9.exe"},  # plain http
    {"url": "https://github.com/o/r/releases/download/v9.9.9/other.exe"},
])
def test_metadata_is_validated(kw: dict[str, str]) -> None:
    raw = _meta(**kw)
    with pytest.raises(UpdateError) as e:
        parse_release(raw, FEED, sign(raw, KEY).encode(), TRUSTED)
    assert e.value.code == "update_bad_metadata"
    good = _meta()
    assert parse_release(good, FEED, sign(good, KEY).encode(), TRUSTED).version == "9.9.9"


# ---- install hand-off ----

def _installed_tree(tmp_path: Path) -> Path:
    app = tmp_path / "inst" / "app"
    (app / "scripts").mkdir(parents=True)
    (app / "scripts" / "apply_update.ps1").write_text("# helper", encoding="utf-8")
    (tmp_path / "inst" / "Uninstall.exe").write_bytes(b"")
    return app


def test_install_hands_over_to_the_hidden_helper(settings: Settings, rel: FakeRelease, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    settings.update_feed_url = rel.feed
    settings.ensure_dirs()
    app = _installed_tree(tmp_path)
    u = Updater(settings, download_dir=tmp_path / "dl", platform="win32", app_root=app,
                spawn=lambda cmd, env: calls.append((list(cmd), env)), trusted_keys=TRUSTED)
    assert install_dir(app) == tmp_path / "inst" and u.status()["can_install"]
    assert _code(u.install) == "update_not_ready"
    v = bump(CUR, 1)
    rel.publish(v)
    u.check()
    u.start_download()
    u.wait(10)
    assert u.install()["state"] == "installing"
    (cmd, env), = calls
    exe = tmp_path / "dl" / f"ResearchOS-Setup-{v}.exe"
    assert cmd[0] == "powershell.exe" and "-WindowStyle" in cmd and cmd[cmd.index("-WindowStyle") + 1] == "Hidden"
    assert cmd[cmd.index("-File") + 1] == str(tmp_path / "dl" / "apply_update.ps1")
    assert cmd[cmd.index("-Installer") + 1] == str(exe)
    assert cmd[cmd.index("-InstallDir") + 1] == str(tmp_path / "inst")
    assert (tmp_path / "dl" / "apply_update.ps1").read_text() == "# helper"  # runs outside app\ (replaced by setup)
    assert not any("KEY" in k.upper() or k.upper().startswith("RESEARCHOS_") for k in env)


def test_install_unsupported_outside_a_windows_install(upd: Updater, rel: FakeRelease, tmp_path: Path) -> None:
    rel.publish(bump(CUR, 1))
    upd.check()
    upd.start_download()
    upd.wait(10)
    assert upd.status()["can_install"] is False
    assert _code(upd.install) == "update_unsupported"
    assert install_dir(tmp_path) is None


# ---- HTTP API ----

def test_http_api_and_no_check_at_startup(app: Any, client: Client, settings: Settings, rel: FakeRelease,
                                          tmp_path: Path) -> None:
    c = client
    st = c.get("/api/v1/update").json()
    assert st["state"] == "idle" and st["latest"] is None and st["auto_check"] is False and rel.hits == []
    app.state.updater = Updater(settings, download_dir=tmp_path / "dl", platform="linux", trusted_keys=TRUSTED)
    settings.update_feed_url = rel.feed
    rel.publish(bump(CUR, 1))
    assert c.post("/api/v1/update/check").json()["state"] == "available"
    r = c.put("/api/v1/update/settings", json={"mirror_prefix": "nope"})
    assert r.status_code == 400 and r.json()["code"] == "update_bad_mirror"
    assert c.post("/api/v1/update/download").status_code == 202
    app.state.updater.wait(10)
    r = c.post("/api/v1/update/install")
    assert r.status_code == 409 and r.json()["code"] == "update_unsupported"
    settings.update_feed_url = f"http://127.0.0.1:{closed_port()}/x"
    r = c.post("/api/v1/update/check")
    assert r.status_code == 502 and r.json()["code"] == "update_network" and r.json()["retryable"] is True


def test_update_requires_a_session(anon: Client) -> None:
    assert anon.get("/api/v1/update").status_code == 401
    assert anon.post("/api/v1/update/check").status_code == 401
