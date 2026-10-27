"""Local end-to-end check of the one-click update (ADR-223/225), offline: an http.server on 127.0.0.1 (random port)
serves latest.json + the installer laid out like a GitHub release, Settings.update_feed_url points at it, and the
real Updater runs: check -> available, download -> sha256 verified -> ready; then a feed with a tampered sha256 must
end in update_checksum_mismatch with the download deleted. The running copy is presented as version 0.0.0 (the
updater only offers newer versions). The install step (silent setup, Windows only) replaces the installed copy and
is always reported as NOT RUN.

Usage: python scripts/release/e2e_check.py [--exe release/ResearchOS-Setup-<v>.exe]   (default: small fake installer)
Exit 0 = PASS."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from researchos import __version__
from researchos.kernel.backup import file_sha256
from researchos.kernel.config import Settings
from researchos.services.ops import updater
from researchos.services.ops.updater import UpdateError, Updater, asset_name

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_json  # noqa: E402
from manifest import write_release  # noqa: E402

SIMULATED_INSTALLED = "0.0.0"
FAKE_INSTALLER = b"MZ fake ResearchOS setup for the release e2e check\n" * 2048
TAMPERED_SHA256 = hashlib.sha256(b"tampered").hexdigest()
DOWNLOAD_TIMEOUT_S = 600.0
_ASSET = re.compile(r"^ResearchOS-Setup-(.+)\.exe$")


@dataclass(frozen=True)
class Step:
    name: str
    status: str  # PASS | FAIL | NOT RUN
    detail: str = ""


def _step(name: str, ok: bool, detail: str) -> Step:
    return Step(name, "PASS" if ok else "FAIL", detail)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib signature)
        pass


@contextmanager
def serve(root: Path) -> Iterator[str]:
    """Serve `root` on 127.0.0.1:<random port>; yields the base URL."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True, name="release-e2e-http").start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def publish_local(root: Path, base: str, installer: Path, version: str, sha256: str | None = None) -> str:
    """<root>/download/v<v>/<asset> + <root>/latest.json (written by the release manifest builder, optionally with a
    tampered sha256); returns the feed URL."""
    exe = root / "download" / f"v{version}" / asset_name(version)
    exe.parent.mkdir(parents=True)
    shutil.copyfile(installer, exe)  # a copy: the release file itself is only read
    feed_url = f"{base}/latest.json"
    meta = write_release(exe, version, "local release e2e check", url=f"{base}/download/v{version}/{exe.name}",
                         feed_url=feed_url, out_dir=root)
    if sha256:
        write_json({**meta, "sha256": sha256}, root / "latest.json")
    return feed_url


def _updater(feed_url: str, work: Path) -> Updater:
    return Updater(Settings(data_dir=work / "data", update_feed_url=feed_url), download_dir=work / "download")


def _check_and_download(up: Updater) -> list[Step]:
    st = up.check()
    steps = [_step("check -> available", st["state"] == "available", f"state={st['state']}")]
    up.start_download()
    up.wait(DOWNLOAD_TIMEOUT_S)
    return steps


def good_run(up: Updater, digest: str) -> list[Step]:
    steps = _check_and_download(up)
    st = up.status()
    ok = st["state"] == "ready" and up.installer is not None and file_sha256(up.installer) == digest
    return [*steps, _step("download -> sha256 verified -> ready", ok, f"state={st['state']} {st['progress']}")]


def tampered_run(up: Updater) -> list[Step]:
    steps = _check_and_download(up)
    code = (up.status()["error"] or {}).get("code")
    left = sorted(p.name for p in up.download_dir.glob("*"))
    return [*steps, _step("download -> update_checksum_mismatch", code == "update_checksum_mismatch",
                          f"error={code}"),
            _step("installer deleted", not left, ", ".join(left) or "download dir empty")]


def _scenario(name: str, body: Callable[[], list[Step]]) -> list[Step]:
    try:
        return [Step(f"{name}: {s.name}", s.status, s.detail) for s in body()]
    except UpdateError as e:
        return [Step(name, "FAIL", f"{e.code}: {e.message}")]


def run(installer: Path | None, work: Path) -> list[Step]:
    """All steps against `installer` (None = a small fake one); `work` is an empty scratch directory."""
    if installer is None:
        installer, version = work / "fake-setup.exe", __version__
        installer.write_bytes(FAKE_INSTALLER)
    else:
        m = _ASSET.match(installer.name)
        if m is None:
            return [Step("installer name", "FAIL", f"{installer.name} is not ResearchOS-Setup-<version>.exe")]
        version = m.group(1)
    digest = file_sha256(installer)
    with patch.object(updater, "__version__", SIMULATED_INSTALLED):
        with serve(work / "srv-good") as base:
            feed = publish_local(work / "srv-good", base, installer, version)
            steps = _scenario("good", lambda: good_run(_updater(feed, work / "good"), digest))
        with serve(work / "srv-tampered") as base:
            feed = publish_local(work / "srv-tampered", base, installer, version, sha256=TAMPERED_SHA256)
            steps += _scenario("tampered", lambda: tampered_run(_updater(feed, work / "tampered")))
    install = Step("install (silent setup hand-off)", "NOT RUN",
                   f"Windows-only and replaces the installed copy; platform here: {sys.platform}")
    return [*steps, install]


def report(steps: list[Step]) -> bool:
    for s in steps:
        print(f"{s.status:8} {s.name}  [{s.detail}]")
    failed = [s for s in steps if s.status == "FAIL"]
    passed = sum(s.status == "PASS" for s in steps)
    not_run = sum(s.status == "NOT RUN" for s in steps)
    print(f"RESULT: {'FAIL' if failed else 'PASS'} ({passed} passed, {len(failed)} failed, {not_run} not run)")
    return not failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, help="real installer; default: a small fake file")
    args = ap.parse_args(argv)
    if args.exe is not None and not args.exe.is_file():
        print(f"e2e_check: {args.exe} not found", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="ros-release-e2e-") as td:
        print(f"simulated installed version {SIMULATED_INSTALLED}; installer: {args.exe or 'fake'}")
        return 0 if report(run(args.exe, Path(td))) else 1


if __name__ == "__main__":
    sys.exit(main())
