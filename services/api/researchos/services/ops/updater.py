r"""One-click update (ADR-223): check -> download + sha256 verify -> silent NSIS install after the app exits.

Nothing here runs unless the owner clicks: there is no startup or background check. The feed is the `latest.json`
asset of the latest GitHub release ({version, asset, sha256, url, notes}); an optional mirror prefix (e.g.
https://ghfast.top/) is prepended to the feed and the installer URL. The installer is downloaded to a temp folder,
hashed while streaming and kept only if the sha256 matches. Installing (Windows, installed copy only) starts a
hidden helper (scripts/apply_update.ps1, copied next to the installer) that stops the app, waits for this process to
exit and runs `ResearchOS-Setup-<v>.exe /S /RUNAFTER`; setup keeps data\ and restores the previous app\ on failure.
Every failure is an UpdateError with a stable code (the web UI translates it; messages are fixed English text).
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from packaging.version import InvalidVersion, Version

from ... import __version__
from ...kernel.backup import file_sha256
from ...kernel.config import REPO_ROOT, Settings
from ...platform.env import child_env
from ...platform.process import hidden_window_kwargs

MAX_FEED_BYTES = 256 * 1024
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
MAX_NOTES_CHARS = 20000
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Fixed messages per code; the UI maps the code (update.errors.<code>), never parses the text.
MESSAGES = {
    "update_network": "could not reach the update server",
    "update_timeout": "the update server did not answer in time",
    "update_http_error": "the update server returned an error",
    "update_bad_metadata": "the release metadata (latest.json) is invalid",
    "update_checksum_mismatch": "the downloaded installer does not match its sha256; it was deleted",
    "update_bad_mirror": "mirror prefix must be empty or an https:// URL ending with /",
    "update_not_available": "check for updates first; no newer version is known",
    "update_busy": "an update download is already running",
    "update_not_ready": "download and verify the update first",
    "update_unsupported": "automatic install needs the Windows installed version; run the downloaded setup manually",
    "update_launch_failed": "the installer could not be started; the current version is unchanged",
}


class UpdateError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(MESSAGES[code])
        self.code, self.message, self.detail = code, MESSAGES[code], detail


@dataclass(frozen=True)
class Release:
    version: str
    asset: str
    sha256: str
    url: str
    notes: str


def _local(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _allowed_url(url: str) -> bool:
    """https anywhere; plain http only to this machine (a local mirror or the tests' fake release server)."""
    u = urlsplit(url)
    return bool(u.hostname) and (u.scheme == "https" or (u.scheme == "http" and _local(u.hostname or "")))


def validate_mirror(prefix: str) -> str:
    prefix = prefix.strip()
    if prefix and not (prefix.endswith("/") and _allowed_url(prefix) and len(prefix) <= 300):
        raise UpdateError("update_bad_mirror")
    return prefix


def asset_name(version: str) -> str:
    """The installer file name for a version (build_package.py / the NSIS OUTFILE produce the same name)."""
    return f"ResearchOS-Setup-{version}.exe"


def parse_release(raw: bytes, feed_url: str) -> Release:
    """latest.json must name ResearchOS-Setup-<version>.exe, a sha256 and a URL on the feed's own host."""
    try:
        d = json.loads(raw.decode("utf-8"))
        rel = Release(str(d["version"]), str(d["asset"]), str(d["sha256"]).lower(), str(d["url"]),
                      str(d.get("notes") or "")[:MAX_NOTES_CHARS])
        Version(rel.version)
    except (ValueError, KeyError, TypeError, InvalidVersion) as e:
        raise UpdateError("update_bad_metadata", type(e).__name__) from e
    u, feed = urlsplit(rel.url), urlsplit(feed_url)
    ok = (rel.asset == asset_name(rel.version) and SHA256.match(rel.sha256) is not None
          and _allowed_url(rel.url) and u.hostname == feed.hostname and u.path.endswith("/" + rel.asset))
    if not ok:
        raise UpdateError("update_bad_metadata", "fields")
    return rel


def is_newer(latest: str, current: str) -> bool:
    return Version(latest) > Version(current)


def install_dir(app_root: Path = REPO_ROOT) -> Path | None:
    """<dir> of a setup-made install (<dir>\\app is the app root, <dir>\\Uninstall.exe exists), else None."""
    return app_root.parent if app_root.name == "app" and (app_root.parent / "Uninstall.exe").is_file() else None


Spawner = Callable[[Sequence[str], dict[str, str]], None]


def _spawn_detached(cmd: Sequence[str], env: dict[str, str]) -> None:
    """Hidden, own process group, outside any job of this process, so it outlives the server it stops."""
    base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined,unused-ignore]
    kw: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                          "close_fds": True, "env": env, **hidden_window_kwargs()}
    try:
        subprocess.Popen(list(cmd), creationflags=base | 0x01000000, **kw)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(list(cmd), creationflags=base, **kw)


class Updater:
    """Owner-triggered update state machine: idle -> checking -> up_to_date | available -> downloading -> ready
    -> installing; any step may end in `error` (the current install is untouched)."""

    def __init__(self, settings: Settings, *, download_dir: Path | None = None, platform: str = sys.platform,
                 app_root: Path = REPO_ROOT, spawn: Spawner = _spawn_detached,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.s = settings
        self.download_dir = download_dir or Path(tempfile.gettempdir()) / "ResearchOS-update"
        self.platform, self.app_root, self.spawn, self.transport = platform, app_root, spawn, transport
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.state = "idle"
        self.release: Release | None = None
        self.received, self.total = 0, 0
        self.error: UpdateError | None = None
        self.installer: Path | None = None

    # ---- settings (mirror prefix: UI choice in <data>/config/update.json wins over researchos.toml) ----
    @property
    def _prefs(self) -> Path:
        return self.s.config_dir / "update.json"

    def mirror_prefix(self) -> str:
        try:
            return str(json.loads(self._prefs.read_text(encoding="utf-8"))["mirror_prefix"])
        except (OSError, ValueError, KeyError):
            return self.s.update_mirror_prefix

    def set_mirror_prefix(self, prefix: str) -> None:
        prefix = validate_mirror(prefix)
        self._prefs.parent.mkdir(parents=True, exist_ok=True)
        self._prefs.write_text(json.dumps({"mirror_prefix": prefix}), encoding="utf-8")

    def _via_mirror(self, url: str) -> str:
        return validate_mirror(self.mirror_prefix()) + url

    def _client(self) -> httpx.Client:
        t = self.s.update_timeout_s
        return httpx.Client(transport=self.transport, timeout=httpx.Timeout(t, connect=min(t, 10.0)),
                            follow_redirects=True, trust_env=False,
                            headers={"user-agent": f"ResearchOS-Updater/{__version__}"})

    # ---- public API (called by the HTTP layer) ----
    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_unlocked()

    def _status_unlocked(self) -> dict[str, Any]:
        return {
            "current_version": __version__, "state": self.state,
            "latest": asdict(self.release) if self.release else None,
            "progress": {"received": self.received, "total": self.total} if self.state in (
                "downloading", "ready", "installing") else None,
            "error": {"code": self.error.code, "message": self.error.message} if self.error else None,
            "mirror_prefix": self.mirror_prefix(), "feed_url": self.s.update_feed_url,
            "can_install": self._install_target() is not None, "auto_check": False,
            "installer": str(self.installer) if self.installer and self.state in ("ready", "installing") else None,
        }

    def check(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "downloading":
                raise UpdateError("update_busy")
            self.state, self.error = "checking", None
        try:
            rel = parse_release(self._get_feed(), self.s.update_feed_url)
        except UpdateError as e:
            self._fail(e)
            raise
        with self._lock:
            self.release = rel
            self.state = "available" if is_newer(rel.version, __version__) else "up_to_date"
            self.installer = self._verified_installer(rel)
            if self.state == "available" and self.installer:
                self.state = "ready"
        return self.status()

    def start_download(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "downloading":
                raise UpdateError("update_busy")
            if self.state == "ready":
                return self._status_unlocked()
            if self.state not in ("available", "error") or self.release is None or not is_newer(
                    self.release.version, __version__):
                raise UpdateError("update_not_available")
            self.state, self.error, self.received, self.total = "downloading", None, 0, 0
            self._thread = threading.Thread(target=self._download_safe, args=(self.release,), daemon=True,
                                            name="update-download")
            self._thread.start()
            return self._status_unlocked()

    def wait(self, timeout: float | None = None) -> None:
        """Tests: wait for a running download to finish."""
        if self._thread:
            self._thread.join(timeout)

    def install(self) -> dict[str, Any]:
        with self._lock:
            if self.state != "ready" or self.installer is None:
                raise UpdateError("update_not_ready")
            target = self._install_target()
            if target is None:
                raise UpdateError("update_unsupported")
            installer = self.installer
        helper = installer.parent / "apply_update.ps1"
        try:
            shutil.copyfile(self.app_root / "scripts" / "apply_update.ps1", helper)
            self.spawn(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                        "-ExecutionPolicy", "Bypass", "-File", str(helper), "-Installer", str(installer),
                        "-InstallDir", str(target), "-ServerPid", str(os.getpid())],
                       child_env(self.s.data_dir / "home"))  # no provider keys in the helper or setup
        except OSError as e:
            raise UpdateError("update_launch_failed", type(e).__name__) from e
        with self._lock:
            self.state = "installing"
        return self.status()

    # ---- internals ----
    def _install_target(self) -> Path | None:
        return install_dir(self.app_root) if self.platform == "win32" else None

    def _fail(self, e: UpdateError) -> None:
        with self._lock:
            self.state, self.error = "error", e

    def _get_feed(self) -> bytes:
        with self._client() as c, _mapped():
            r = c.get(self._via_mirror(self.s.update_feed_url))
            if r.status_code != 200:
                raise UpdateError("update_http_error", str(r.status_code))
            if len(r.content) > MAX_FEED_BYTES:
                raise UpdateError("update_bad_metadata", "too large")
            return r.content

    def _target(self, rel: Release) -> Path:
        return self.download_dir / rel.asset

    def _verified_installer(self, rel: Release) -> Path | None:
        """A previously downloaded installer is reused only if its hash still matches."""
        p = self._target(rel)
        return p if p.is_file() and file_sha256(p) == rel.sha256 else None

    def _download_safe(self, rel: Release) -> None:
        try:
            path = self._download(rel)
        except UpdateError as e:
            self._fail(e)
            return
        with self._lock:
            self.installer, self.state = path, "ready"

    def _download(self, rel: Release) -> Path:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        part = self._target(rel).with_suffix(".part")
        h = hashlib.sha256()
        try:
            with self._client() as c, _mapped(), c.stream("GET", self._via_mirror(rel.url)) as r:
                if r.status_code != 200:
                    raise UpdateError("update_http_error", str(r.status_code))
                with self._lock:
                    self.total = int(r.headers.get("content-length") or 0)
                with part.open("wb") as fh:
                    for chunk in r.iter_bytes(1 << 16):
                        fh.write(chunk)
                        h.update(chunk)
                        with self._lock:
                            self.received += len(chunk)
                        if self.received > MAX_INSTALLER_BYTES:
                            raise UpdateError("update_bad_metadata", "installer too large")
            if h.hexdigest() != rel.sha256:
                raise UpdateError("update_checksum_mismatch")
            return part.replace(self._target(rel))
        finally:
            part.unlink(missing_ok=True)


@contextmanager
def _mapped() -> Iterator[None]:
    """Map httpx failures to stable codes (no URL or proxy text reaches the client)."""
    try:
        yield
    except httpx.TimeoutException as e:
        raise UpdateError("update_timeout", type(e).__name__) from e
    except httpx.HTTPError as e:
        raise UpdateError("update_network", type(e).__name__) from e

