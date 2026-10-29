"""Update hardening (ADR-228): Ed25519 feed signature, failure paths of the download (cut off mid-stream, disk full,
asset missing), mirror down -> owner retries without it, two racing clicks, the one-time "What's new" and the HTTP
surface of these. Local fake release server only; no internet."""

from __future__ import annotations

import base64
import errno
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from researchos.kernel.config import Settings
from researchos.services.ops import updater as updater_mod
from researchos.services.ops.update_feed import MESSAGES, UpdateError
from researchos.services.ops.update_signing import TRUSTED_KEYS, SignatureError, key_id, public_b64, sign, verify
from researchos.services.ops.updater import Updater

from .conftest import Client
from .fake_release import CUR as CURRENT
from .fake_release import INSTALLER, KEY, TRUSTED, FakeRelease, bump, closed_port

NEW = bump(CURRENT, 1)


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


def _download(u: Updater, **kw: bool) -> dict[str, Any]:
    u.start_download(**kw)
    u.wait(10)
    return u.status()


# ---- signature primitives ----

def test_signature_verifies_and_rejects() -> None:
    data = b'{"version": "9.9.9"}'
    sig = sign(data, KEY).encode()
    assert verify(data, sig, TRUSTED) == key_id(KEY.public_key())
    other = Ed25519PrivateKey.generate()
    forged = sign(data, other).replace(key_id(other.public_key()), key_id(KEY.public_key())).encode()
    cases = {None: "unsigned", b"": "unsigned", b"garbage": "unsigned", b"ed25519 k !!!": "unsigned",
             sign(data, other).encode(): "bad_signature",  # unknown key id
             forged: "bad_signature", sig.replace(b"ed25519", b"rsa"): "bad_signature"}
    for s, kind in cases.items():
        with pytest.raises(SignatureError) as e:
            verify(data, s, TRUSTED)
        assert e.value.kind == kind, s
    with pytest.raises(SignatureError) as e:
        verify(data + b" ", sig, TRUSTED)  # tampered bytes
    assert e.value.kind == "bad_signature"


def test_embedded_keys_are_release_and_backup_ed25519_keys() -> None:
    assert len(TRUSTED_KEYS) == 2  # release + backup (a lost release key does not lock out updates)
    for kid, pub in TRUSTED_KEYS.items():
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub))
        assert key_id(key) == kid and public_b64(key) == pub


def test_the_default_updater_trusts_only_the_embedded_keys(settings: Settings, rel: FakeRelease) -> None:
    """A feed signed by any other key (here: the tests' key) is refused by the production configuration."""
    settings.update_feed_url = rel.feed
    rel.publish(NEW)
    u = Updater(settings, platform="linux")
    assert _code(u.check) == "update_bad_signature" and u.status()["latest"] is None


# ---- unsigned / tampered feeds are never offered ----

def test_unsigned_feed_is_refused(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(NEW)
    del rel.routes[rel.feed_path + ".sig"]
    assert _code(upd.check) == "update_unsigned"
    st = upd.status()
    assert st["state"] == "error" and st["latest"] is None and _code(upd.start_download) == "update_not_available"


def test_feed_changed_after_signing_is_refused(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(NEW)
    status, body, delay = rel.routes[rel.feed_path]
    rel.routes[rel.feed_path] = (status, body.replace(b"notes for", b"NOTES FOR"), delay)
    assert _code(upd.check) == "update_bad_signature"
    assert upd.status()["error"] == {"code": "update_bad_signature", "message": MESSAGES["update_bad_signature"]}


# ---- download failure paths ----

def test_download_cut_off_mid_stream_is_incomplete_and_deleted(upd: Updater, rel: FakeRelease, tmp_path: Path) -> None:
    rel.publish(NEW, body=INSTALLER[:5000])
    rel.declared[rel.asset_path(NEW)] = len(INSTALLER)  # announces the full size, sends 5000 bytes, closes
    upd.check()
    st = _download(upd)
    assert st["state"] == "error" and st["error"]["code"] == "update_incomplete"
    assert not list((tmp_path / "dl").iterdir())


@pytest.mark.parametrize(("err", "code"), [(errno.ENOSPC, "update_disk_full"), (errno.EACCES, "update_write_failed")])
def test_disk_errors_on_the_part_file(upd: Updater, rel: FakeRelease, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      err: int, code: str) -> None:
    real_open = Path.open

    class Full:
        def __init__(self, fh: Any) -> None:
            self.fh = fh

        def __enter__(self) -> Full:
            return self

        def __exit__(self, *a: object) -> None:
            self.fh.close()

        def write(self, b: bytes) -> int:
            raise OSError(err, "simulated")

    def fake_open(self: Path, mode: str = "r", *a: Any, **k: Any) -> Any:
        fh = real_open(self, mode, *a, **k)
        return Full(fh) if self.suffix == ".part" else fh

    rel.publish(NEW)
    upd.check()
    monkeypatch.setattr(Path, "open", fake_open)
    st = _download(upd)
    assert st["state"] == "error" and st["error"]["code"] == code
    assert not list((tmp_path / "dl").glob("*.part"))


# ---- mirror down: clear code, the owner retries without it (never automatic) ----

def test_mirror_down_then_owner_retries_direct(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(NEW)
    upd.set_mirror_prefix(f"http://127.0.0.1:{closed_port()}/")
    assert _code(upd.check) == "update_mirror_failed"
    assert not [h for h in rel.hits if not h.startswith("/mirror/")]  # no silent fallback to the origin
    assert upd.check(direct=True)["state"] == "available"
    st = _download(upd)
    assert st["error"]["code"] == "update_mirror_failed"
    assert _download(upd, direct=True)["state"] == "ready"
    assert upd.mirror_prefix().startswith("http://127.0.0.1:")  # the setting itself is unchanged


def test_mirror_http_error_is_a_mirror_failure(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(NEW)
    upd.set_mirror_prefix(rel.base + "/mirror/")
    rel.routes[rel.feed_path] = (502, b"bad gateway", 0.0)
    assert _code(upd.check) == "update_mirror_failed"


# ---- two racing clicks ----

def test_two_racing_download_clicks_start_one_download(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(NEW)
    upd.check()
    status, body, _ = rel.routes[rel.asset_path(NEW)]
    rel.routes[rel.asset_path(NEW)] = (status, body, 0.5)
    gate, results = threading.Barrier(2), []

    def click() -> None:
        gate.wait()
        try:
            results.append(upd.start_download()["state"])
        except UpdateError as e:
            results.append(e.code)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert sorted(results) == ["downloading", "update_busy"]
    assert _code(upd.check) == "update_busy"  # a check cannot interleave with the running download
    upd.wait(10)
    assert upd.status()["state"] == "ready"
    assert rel.hits.count(rel.asset_path(NEW)) == 1


# ---- "What's new" after the update, shown once ----

def _installed(tmp_path: Path) -> Path:
    app = tmp_path / "inst" / "app"
    (app / "scripts").mkdir(parents=True)
    (app / "scripts" / "apply_update.ps1").write_text("# helper", encoding="utf-8")
    (tmp_path / "inst" / "Uninstall.exe").write_bytes(b"")
    return app


def test_whats_new_is_kept_at_install_and_shown_once_on_the_new_version(
        settings: Settings, rel: FakeRelease, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.update_feed_url = rel.feed
    settings.ensure_dirs()
    calls: list[list[str]] = []
    u = Updater(settings, download_dir=tmp_path / "dl", platform="win32", app_root=_installed(tmp_path),
                spawn=lambda cmd, env: calls.append(list(cmd)), trusted_keys=TRUSTED)
    rel.publish(NEW, notes_zh=f"{NEW} 的更新说明")
    assert u.check()["latest"]["notes_zh"] == f"{NEW} 的更新说明"
    _download(u)
    u.install()
    (cmd,) = calls
    assert cmd[cmd.index("-Version") + 1] == NEW
    assert cmd[cmd.index("-HealthUrl") + 1] == f"http://127.0.0.1:{settings.port}/api/v1/health"
    assert u.status()["whats_new"] is None  # still the old version (the update has not run yet)
    monkeypatch.setattr(updater_mod, "__version__", NEW)  # the restarted, updated app
    after = Updater(settings, platform="win32", trusted_keys=TRUSTED)
    assert after.status()["whats_new"] == {"version": NEW, "notes": f"notes for {NEW}", "notes_zh": f"{NEW} 的更新说明"}
    assert after.dismiss_whats_new()["whats_new"] is None
    assert Updater(settings, platform="win32").status()["whats_new"] is None


def test_feed_without_chinese_notes(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(NEW)
    assert upd.check()["latest"]["notes_zh"] == ""


# ---- HTTP ----

def test_http_direct_retry_codes_and_dismiss(app: Any, client: Client, settings: Settings, rel: FakeRelease,
                                            tmp_path: Path) -> None:
    settings.update_feed_url = rel.feed
    app.state.updater = u = Updater(settings, download_dir=tmp_path / "dl", platform="linux", trusted_keys=TRUSTED)
    rel.publish(NEW)
    u.set_mirror_prefix(f"http://127.0.0.1:{closed_port()}/")
    r = client.post("/api/v1/update/check")
    assert r.status_code == 502 and r.json()["code"] == "update_mirror_failed" and r.json()["retryable"] is False
    assert client.post("/api/v1/update/check", json={"direct": True}).json()["state"] == "available"
    assert client.post("/api/v1/update/download", json={"direct": True}).status_code == 202
    u.wait(10)
    assert client.get("/api/v1/update").json()["state"] == "ready"
    r = client.post("/api/v1/update/whats-new/dismiss")
    assert r.status_code == 200 and r.json()["whats_new"] is None


# ---- oversized feed / signature: refused while streaming, never buffered whole (Review #95) ----

@pytest.mark.parametrize(("route", "code"), [(".sig", "update_unsigned"), ("", "update_bad_metadata")])
def test_oversized_feed_or_signature_announced_by_content_length(upd: Updater, rel: FakeRelease, route: str,
                                                                 code: str) -> None:
    rel.publish(NEW)
    status, body, delay = rel.routes[rel.feed_path + route]
    rel.routes[rel.feed_path + route] = (status, body + b" " * (2 * updater_mod.MAX_FEED_BYTES), delay)
    assert _code(upd.check) == code


@pytest.mark.parametrize(("limit", "code"), [(updater_mod.MAX_SIG_BYTES, "update_unsigned"),
                                             (updater_mod.MAX_FEED_BYTES, "update_bad_metadata")])
def test_a_chunked_oversized_response_stops_reading_at_the_limit(limit: int, code: str) -> None:
    """No content-length (chunked): reading stops one chunk past the limit instead of buffering the whole body."""
    pulled = []

    def endless() -> Iterator[bytes]:
        while True:
            pulled.append(1)
            assert len(pulled) < 10_000, "the client kept reading"
            yield b"x" * 4096

    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=endless()))
    with httpx.Client(transport=transport) as c, pytest.raises(UpdateError) as e:
        updater_mod._bounded(c, "http://mirror.invalid/latest.json.sig", limit, code)
    assert e.value.code == code and len(pulled) * 4096 <= limit + 2 * 16384 + 4096


# ---- redirects followed by hand, their bodies never read (Review #96) ----

def _redirecting(pulled: list[int], hops: int) -> httpx.MockTransport:
    """`/r<n>` redirects to `/r<n+1>` with an endless body; `/r<hops>` answers 200 `ok`."""
    def endless() -> Iterator[bytes]:
        while True:
            pulled.append(1)
            assert len(pulled) < 10_000, "the client kept reading a redirect body"
            yield b"x" * 4096

    def handler(req: httpx.Request) -> httpx.Response:
        n = int(req.url.path.removeprefix("/r"))
        if n >= hops:
            return httpx.Response(200, content=b"ok")
        return httpx.Response(302, headers={"location": f"/r{n + 1}"}, content=endless())
    return httpx.MockTransport(handler)


def test_a_redirect_body_is_not_read(upd: Updater) -> None:
    pulled: list[int] = []
    upd.transport = _redirecting(pulled, 2)
    with upd._client() as c:
        assert c.follow_redirects is False  # httpx would read each redirect body whole
        assert updater_mod._bounded(c, "http://mirror.invalid/r0", 16, "update_bad_metadata") == (200, b"ok")
    assert pulled == []


def test_too_many_redirects_is_a_network_error(upd: Updater) -> None:
    pulled: list[int] = []
    upd.transport = _redirecting(pulled, updater_mod.MAX_REDIRECTS + 1)
    with pytest.raises(UpdateError) as e, upd._client() as c, updater_mod._mapped():
        updater_mod._bounded(c, "http://mirror.invalid/r0", 16, "update_bad_metadata")
    assert e.value.code == "update_network" and pulled == []


def test_small_feed_and_404_signature_are_read_normally(upd: Updater, rel: FakeRelease) -> None:
    rel.publish(NEW)
    del rel.routes[rel.feed_path + ".sig"]  # 404: an unsigned release, refused by the signature check
    assert _code(upd.check) == "update_unsigned"
