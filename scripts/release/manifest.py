"""Release manifest (ADR-225/228): for release/ResearchOS-Setup-<v>.exe write, next to it,
ResearchOS-Setup-<v>.exe.sha256 ("<hex>  <name>"), latest.json = the updater feed {version, asset, sha256, url,
notes, notes_zh} (ADR-223) and latest.json.sig, its detached Ed25519 signature made with the release key (kept
outside the repo: $RESEARCHOS_UPDATE_KEY, default ~/.config/researchos/keys/update-release.pem). url =
https://github.com/<owner>/<repo>/releases/download/v<v>/<asset> (owner/repo from the default Settings.update_feed_url).
Signed feed + signature are checked with update_feed.parse_release (embedded trusted keys) before anything is written.

Usage: python scripts/release/manifest.py (--notes TEXT | --notes-file FILE) [--notes-zh-file FILE] [--exe PATH]
           [--key PEM]   (default exe: release/ResearchOS-Setup-<pyproject version>.exe)"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from researchos import __version__
from researchos.kernel.backup import file_sha256
from researchos.services.ops.update_feed import UpdateError, asset_name, parse_release, signature_url
from researchos.services.ops.update_signing import TRUSTED_KEYS, load_private, sign

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FEED_URL, SIGNING_KEY, default_exe, download_url, json_bytes, sha256_line  # noqa: E402

FEED, SIG = "latest.json", Path(signature_url("latest.json")).name


def feed(exe: Path, version: str, url: str, notes: str, notes_zh: str = "") -> dict[str, str]:
    return {"version": version, "asset": exe.name, "sha256": file_sha256(exe), "url": url, "notes": notes,
            "notes_zh": notes_zh}


def signed(meta: dict[str, str], key: Ed25519PrivateKey, feed_url: str,
           trusted: Mapping[str, str] = TRUSTED_KEYS) -> tuple[bytes, bytes]:
    """(feed bytes, signature) that the updater accepts with its embedded keys (raises UpdateError otherwise)."""
    raw = json_bytes(meta)
    sig = sign(raw, key).encode("ascii")
    parse_release(raw, feed_url, sig, trusted)
    return raw, sig


def write_release(exe: Path, version: str, notes: str, *, url: str, feed_url: str, out_dir: Path,
                  key: Ed25519PrivateKey, notes_zh: str = "", trusted: Mapping[str, str] = TRUSTED_KEYS,
                  ) -> dict[str, str]:
    """Write <asset>.sha256, latest.json and latest.json.sig for `exe` into `out_dir`; returns the feed."""
    if exe.name != asset_name(version):
        raise ValueError(f"{exe.name} is not {asset_name(version)}")
    meta = feed(exe, version, url, notes, notes_zh)
    raw, sig = signed(meta, key, feed_url, trusted)
    (out_dir / f"{exe.name}.sha256").write_text(sha256_line(meta["sha256"], exe.name), encoding="utf-8")
    (out_dir / FEED).write_bytes(raw)
    (out_dir / SIG).write_bytes(sig)
    return meta


def _text(inline: str | None, path: str | None) -> str:
    return Path(path).read_text(encoding="utf-8").strip() if path else str(inline or "").strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, default=default_exe())
    ap.add_argument("--key", type=Path, default=SIGNING_KEY)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--notes")
    group.add_argument("--notes-file")
    ap.add_argument("--notes-zh-file", help="Chinese release notes for the in-app 'What's new'")
    args = ap.parse_args(argv)
    exe: Path = args.exe
    for p, hint in ((exe, "build it with scripts/build_package.py"), (args.key, "create it with keygen.py")):
        if not p.is_file():
            print(f"manifest: {p} not found ({hint})", file=sys.stderr)
            return 2
    try:
        meta = write_release(exe, __version__, _text(args.notes, args.notes_file), url=download_url(__version__),
                             feed_url=FEED_URL, out_dir=exe.parent, key=load_private(args.key.read_bytes()),
                             notes_zh=_text(None, args.notes_zh_file))
    except (ValueError, UpdateError) as e:
        print(f"manifest: {e}", file=sys.stderr)
        return 1
    print(f"{exe.parent / FEED} (+ {SIG}): {meta['version']} {meta['sha256']}\n{meta['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
