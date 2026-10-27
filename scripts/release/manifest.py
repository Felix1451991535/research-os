"""Release manifest (ADR-225): for release/ResearchOS-Setup-<v>.exe write, next to it,
ResearchOS-Setup-<v>.exe.sha256 ("<hex>  <name>") and latest.json = the updater feed {version, asset, sha256, url,
notes} (ADR-223), with url = https://github.com/<owner>/<repo>/releases/download/v<v>/<asset> (owner/repo from the
default Settings.update_feed_url). The feed is checked with updater.parse_release before anything is written.

Usage: python scripts/release/manifest.py (--notes TEXT | --notes-file FILE) [--exe PATH]
       (default exe: release/ResearchOS-Setup-<pyproject version>.exe)"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from researchos import __version__
from researchos.kernel.backup import file_sha256
from researchos.services.ops.updater import UpdateError, asset_name, parse_release

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FEED_URL, default_exe, download_url, sha256_line, write_json  # noqa: E402


def feed(exe: Path, version: str, url: str, notes: str) -> dict[str, str]:
    return {"version": version, "asset": exe.name, "sha256": file_sha256(exe), "url": url, "notes": notes}


def validated(meta: dict[str, str], feed_url: str) -> dict[str, str]:
    """The updater must accept exactly what is published (raises UpdateError otherwise)."""
    parse_release(json.dumps(meta).encode("utf-8"), feed_url)
    return meta


def write_release(exe: Path, version: str, notes: str, *, url: str, feed_url: str, out_dir: Path) -> dict[str, str]:
    """Write <out_dir>/<asset>.sha256 and <out_dir>/latest.json for `exe`; returns the feed."""
    if exe.name != asset_name(version):
        raise ValueError(f"{exe.name} is not {asset_name(version)}")
    meta = validated(feed(exe, version, url, notes), feed_url)
    (out_dir / f"{exe.name}.sha256").write_text(sha256_line(meta["sha256"], exe.name), encoding="utf-8")
    write_json(meta, out_dir / "latest.json")
    return meta


def _notes(args: argparse.Namespace) -> str:
    return Path(args.notes_file).read_text(encoding="utf-8").strip() if args.notes_file else str(args.notes).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, default=default_exe())
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--notes")
    group.add_argument("--notes-file")
    args = ap.parse_args(argv)
    exe: Path = args.exe
    if not exe.is_file():
        print(f"manifest: {exe} not found (build it with scripts/build_package.py)", file=sys.stderr)
        return 2
    try:
        meta = write_release(exe, __version__, _notes(args), url=download_url(__version__), feed_url=FEED_URL,
                             out_dir=exe.parent)
    except (ValueError, UpdateError) as e:
        print(f"manifest: {e}", file=sys.stderr)
        return 1
    print(f"{exe.parent / 'latest.json'}: {meta['version']} {meta['sha256']}\n{meta['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
