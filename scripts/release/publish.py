"""Publish a release (ADR-225/228): tag v<v>, push the tag, `gh release create` with the exe, its .sha256, latest.json
and latest.json.sig. DRY RUN by default (preflight + the commands are printed, nothing runs); --yes executes them.
Preflight: the four files exist, latest.json + its signature pass update_feed.parse_release (embedded keys), its
version is the pyproject version, the exe and the .sha256 file match its sha256, the worktree is clean and the tag
does not exist yet. The repo comes from the default Settings.update_feed_url (gh --repo), notes from latest.json.

Usage: python scripts/release/publish.py [--dir release] [--yes]"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from researchos import __version__
from researchos.kernel.backup import file_sha256
from researchos.services.ops.update_feed import Release, UpdateError, asset_name, parse_release, signature_url

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FEED_URL, RELEASE_DIR, ROOT, github_repo, run, sha256_line, show  # noqa: E402


def files(rel_dir: Path, version: str) -> list[Path]:
    exe = rel_dir / asset_name(version)
    latest = rel_dir / "latest.json"
    return [exe, exe.with_name(exe.name + ".sha256"), latest, Path(signature_url(str(latest)))]


def load_feed(latest: Path) -> Release:
    sig = Path(signature_url(str(latest)))
    return parse_release(latest.read_bytes(), FEED_URL, sig.read_bytes() if sig.is_file() else None)


def notes_of(latest: Path) -> str:
    try:
        return load_feed(latest).notes
    except (OSError, UpdateError):
        return "<notes from latest.json>"


def file_problems(paths: list[Path], version: str) -> list[str]:
    missing = [f"missing {p}" for p in paths if not p.is_file()]
    if missing:
        return missing
    exe, sha_file, latest, _sig = paths
    try:
        rel = load_feed(latest)
    except UpdateError as e:
        return [f"{latest}: {e.message} ({e.detail})"]
    digest = file_sha256(exe)
    checks = {f"latest.json version {rel.version} != {version}": rel.version == version,
              f"latest.json sha256 does not match {exe.name}": rel.sha256 == digest,
              f"{sha_file.name} does not match {exe.name}": sha_file.read_text(encoding="utf-8") == sha256_line(
                  digest, exe.name)}
    return [msg for msg, ok in checks.items() if not ok]


def git_problems(tag: str) -> list[str]:
    out = []
    if run(["git", "status", "--porcelain", "--untracked-files=no"]):
        out.append("worktree has uncommitted changes")
    if run(["git", "tag", "--list", tag]):
        out.append(f"tag {tag} already exists")
    return out


def commands(paths: list[Path], version: str, notes: str) -> list[list[str]]:
    tag = f"v{version}"
    return [
        ["git", "tag", "-a", tag, "-m", f"Research OS {version}"],
        ["git", "push", "origin", tag],
        ["gh", "release", "create", tag, "--repo", github_repo(), "--verify-tag", "--latest",
         "--title", f"Research OS {version}", "--notes", notes, *map(str, paths)],
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=RELEASE_DIR)
    ap.add_argument("--yes", action="store_true", help="really tag, push the tag and create the GitHub release")
    args = ap.parse_args(argv)
    paths = files(args.dir, __version__)
    problems = file_problems(paths, __version__) + git_problems(f"v{__version__}")
    for p in problems:
        print(f"PREFLIGHT FAIL: {p}", file=sys.stderr)
    cmds = commands(paths, __version__, notes_of(paths[2]))
    print(("EXECUTING" if args.yes and not problems else "DRY RUN (nothing is executed; add --yes)") + ":")
    for cmd in cmds:
        print("  " + show(cmd))
    if problems:
        return 1
    if args.yes:
        for cmd in cmds:
            subprocess.run(cmd, cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
