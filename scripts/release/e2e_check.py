"""Local end-to-end check of the one-click update (ADR-223/225/228), offline: an http.server on 127.0.0.1 (random
port) serves latest.json (+ latest.json.sig) and the installer laid out like a GitHub release, Settings.update_feed_url
points at it, and the real Updater runs, trusting only an ephemeral test key generated for this run:
  signed            check -> available, download -> sha256 verified -> ready
  unsigned          no latest.json.sig -> update_unsigned
  tampered-signature  feed bytes changed after signing -> update_bad_signature
  tampered-sha256   signed feed with a wrong sha256 -> update_checksum_mismatch, download deleted
  mirror-down       mirror prefix unreachable -> update_mirror_failed; the owner's retry without it -> available
  truncated         the server announces the full installer, sends half and closes -> update_incomplete, deleted
  asset-missing     signed feed names a newer version whose installer answers 404 -> update_asset_missing
  racing-clicks     two download clicks at once (slow server) -> one download, the other update_busy -> ready
(disk full on the .part file is a unit test only: tests/test_update_hardening.py)
The running copy is presented as version 0.0.0 (the updater only offers newer versions). The install step (silent
setup, Windows only) replaces the installed copy and is always reported as NOT RUN.

Usage: python scripts/release/e2e_check.py [--exe release/ResearchOS-Setup-<v>.exe]   (default: small fake installer)
Exit 0 = PASS."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from researchos import __version__
from researchos.kernel.backup import file_sha256
from researchos.kernel.config import Settings
from researchos.services.ops import updater
from researchos.services.ops.update_feed import UpdateError, asset_name
from researchos.services.ops.update_signing import key_id, public_b64
from researchos.services.ops.updater import Updater

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import signed, write_release  # noqa: E402

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


@dataclass(frozen=True)
class Faults:
    """Server-side faults for the installer (.exe) responses."""
    cut_after: int | None = None  # send this many bytes of the announced full length, then close
    delay_s: float = 0.0          # wait before answering (a slow server)


class _QuietHandler(SimpleHTTPRequestHandler):
    faults = Faults()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib signature)
        pass

    def send_head(self) -> Any:
        if self.path.endswith(".exe"):
            time.sleep(self.faults.delay_s)
        return super().send_head()

    def copyfile(self, source: Any, outputfile: Any) -> None:
        if self.path.endswith(".exe") and self.faults.cut_after is not None:
            outputfile.write(source.read(self.faults.cut_after))  # content-length still announces the full file
            self.close_connection = True
            return
        super().copyfile(source, outputfile)


@contextmanager
def serve(root: Path, faults: Faults = Faults()) -> Iterator[str]:  # noqa: B008 (frozen, immutable default)
    """Serve `root` on 127.0.0.1:<random port>; yields the base URL."""
    handler = type("Handler", (_QuietHandler,), {"faults": faults})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True, name="release-e2e-http").start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@dataclass(frozen=True)
class Signer:
    key: Ed25519PrivateKey
    trusted: dict[str, str]

    @classmethod
    def ephemeral(cls) -> Signer:
        key = Ed25519PrivateKey.generate()
        return cls(key, {key_id(key.public_key()): public_b64(key.public_key())})


def publish_local(root: Path, base: str, installer: Path, version: str, signer: Signer, *, mode: str = "signed") -> str:
    """<root>/download/v<v>/<asset> + <root>/latest.json(.sig) written by the release manifest builder, then altered
    per `mode` (signed | unsigned | tampered-signature | tampered-sha256 | asset-missing); returns the feed URL."""
    exe = root / "download" / f"v{version}" / asset_name(version)
    exe.parent.mkdir(parents=True)
    shutil.copyfile(installer, exe)  # a copy: the release file itself is only read
    feed_url = f"{base}/latest.json"
    meta = write_release(exe, version, "local release e2e check", url=f"{base}/download/v{version}/{exe.name}",
                         feed_url=feed_url, out_dir=root, key=signer.key, trusted=signer.trusted)
    feed, sig = root / "latest.json", root / "latest.json.sig"
    if mode == "unsigned":
        sig.unlink()
    elif mode == "tampered-signature":
        feed.write_bytes(feed.read_bytes().replace(b"local release e2e check", b"local release e2e CHECK"))
    elif mode == "asset-missing":
        exe.unlink()
    elif mode == "tampered-sha256":
        raw, s = signed({**meta, "sha256": TAMPERED_SHA256}, signer.key, feed_url, signer.trusted)
        feed.write_bytes(raw)
        sig.write_bytes(s)
    return feed_url


def _updater(feed_url: str, work: Path, signer: Signer, mirror: str = "") -> Updater:
    s = Settings(data_dir=work / "data", update_feed_url=feed_url, update_mirror_prefix=mirror, update_timeout_s=5.0)
    return Updater(s, download_dir=work / "download", trusted_keys=signer.trusted)


def refused(up: Updater, code: str) -> list[Step]:
    try:
        st = up.check()
    except UpdateError as e:
        return [_step(f"check -> {code}", e.code == code, f"error={e.code}")]
    return [_step(f"check -> {code}", False, f"state={st['state']}")]


def mirror_down_run(up: Updater) -> list[Step]:
    steps = refused(up, "update_mirror_failed")
    st = up.check(direct=True)
    return [*steps, _step("retry without the mirror -> available", st["state"] == "available", f"state={st['state']}")]


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


def failed_download_run(up: Updater, code: str) -> list[Step]:
    steps = _check_and_download(up)
    got = (up.status()["error"] or {}).get("code")
    left = sorted(p.name for p in up.download_dir.glob("*"))
    return [*steps, _step(f"download -> {code}", got == code, f"error={got}"),
            _step("nothing left in the download dir", not left, ", ".join(left) or "download dir empty")]


def racing_clicks_run(up: Updater) -> list[Step]:
    st = up.check()
    gate, results = threading.Barrier(2), []

    def click() -> None:
        gate.wait()
        try:
            results.append(up.start_download()["state"])
        except UpdateError as e:
            results.append(e.code)

    clicks = [threading.Thread(target=click) for _ in range(2)]
    for t in clicks:
        t.start()
    for t in clicks:
        t.join(DOWNLOAD_TIMEOUT_S)
    up.wait(DOWNLOAD_TIMEOUT_S)
    final = up.status()["state"]
    return [_step("check -> available", st["state"] == "available", f"state={st['state']}"),
            _step("two clicks -> one download + update_busy", sorted(results) == ["downloading", "update_busy"],
                  ", ".join(sorted(results))),
            _step("the one download -> ready", final == "ready", f"state={final}")]


def _scenario(name: str, body: Callable[[], list[Step]]) -> list[Step]:
    try:
        return [Step(f"{name}: {s.name}", s.status, s.detail) for s in body()]
    except UpdateError as e:
        return [Step(name, "FAIL", f"{e.code}: {e.message}")]


def _closed_port_url() -> str:
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sk.getsockname()[1]}/mirror/"


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
    digest, signer = file_sha256(installer), Signer.ephemeral()
    size = installer.stat().st_size
    scenarios: dict[str, tuple[str, Callable[[Updater], list[Step]], str, Faults]] = {
        "signed": ("signed", lambda up: good_run(up, digest), "", Faults()),
        "unsigned": ("unsigned", lambda up: refused(up, "update_unsigned"), "", Faults()),
        "tampered-signature": ("tampered-signature", lambda up: refused(up, "update_bad_signature"), "", Faults()),
        "tampered-sha256": ("tampered-sha256", tampered_run, "", Faults()),
        "mirror-down": ("signed", mirror_down_run, _closed_port_url(), Faults()),
        "truncated": ("signed", lambda up: failed_download_run(up, "update_incomplete"), "",
                      Faults(cut_after=size // 2)),
        "asset-missing": ("asset-missing", lambda up: failed_download_run(up, "update_asset_missing"), "", Faults()),
        "racing-clicks": ("signed", racing_clicks_run, "", Faults(delay_s=0.5)),
    }
    steps: list[Step] = []
    with patch.object(updater, "__version__", SIMULATED_INSTALLED):
        for name, (mode, body, mirror, faults) in scenarios.items():
            with serve(work / f"srv-{name}", faults) as base:
                feed = publish_local(work / f"srv-{name}", base, installer, version, signer, mode=mode)
                steps += _scenario(name, partial(body, _updater(feed, work / name, signer, mirror)))
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
