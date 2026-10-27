"""Filesystem primitives for K4 (closed set: mkdir, copy, move, trash). Never overwrite (publish by hard link,
which fails if the target exists), never delete user data (trash via send2trash; only our own *.ros-partial temp
files are unlinked), re-hash right before acting (TOCTOU), verify the destination hash after acting."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

from ...platform import sysinfo

PARTIAL = ".ros-partial"


class Blocked(Exception):
    """The OS refused for a transient reason (e.g. Windows sharing violation): pause the plan, run nothing else."""


class Conflict(Exception):
    """Precondition or post-condition failed: the world differs from the plan/journal. Never forced."""


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _guard(e: OSError) -> None:
    if getattr(e, "winerror", None) == 32 or e.errno in (errno.EBUSY, errno.ETXTBSY):
        raise Blocked("sharing_violation: the file is open in another program; close it and resume") from e


def partial_of(dst: Path) -> Path:
    return dst.with_name(dst.name + PARTIAL)


def _check_src(src: Path, sha: str) -> None:
    if not src.is_file():
        raise Conflict(f"source missing: {src}")
    if sha256_file(src) != sha:
        raise Conflict(f"source changed since the plan was made: {src}")


def _publish(tmp: Path, dst: Path) -> None:
    """Atomic no-overwrite publish of tmp at dst (same directory)."""
    try:
        os.link(tmp, dst)
        tmp.unlink()
    except FileExistsError as e:
        raise Conflict(f"destination appeared meanwhile: {dst}") from e
    except OSError as e:
        if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EXDEV, errno.EOPNOTSUPP):
            _guard(e)
            raise
        if dst.exists():  # file system without hard links: check, then rename (Windows rename never overwrites)
            raise Conflict(f"destination appeared meanwhile: {dst}") from e
        os.rename(tmp, dst)


def copy(src: Path, dst: Path, sha: str) -> dict[str, Any]:
    _check_src(src, sha)
    if dst.exists():
        raise Conflict(f"destination exists (never overwritten): {dst}")
    tmp = partial_of(dst)
    tmp.unlink(missing_ok=True)  # our own leftover from an interrupted run
    try:
        shutil.copyfile(src, tmp)
        shutil.copystat(src, tmp)
        with tmp.open("rb") as f:
            os.fsync(f.fileno())
    except OSError as e:
        tmp.unlink(missing_ok=True)
        _guard(e)
        raise
    if sha256_file(tmp) != sha:
        tmp.unlink(missing_ok=True)
        raise Conflict(f"copy verification failed: {dst}")
    _publish(tmp, dst)
    return {"dst_sha256": sha}


def move(src: Path, dst: Path, sha: str) -> dict[str, Any]:
    _check_src(src, sha)
    if dst.exists():
        raise Conflict(f"destination exists (never overwritten): {dst}")
    try:
        os.link(src, dst)  # same volume: no data copy, no overwrite
        os.unlink(src)     # the data lives on under dst
        return {"dst_sha256": sha, "via": "link"}
    except OSError as e:
        if isinstance(e, FileExistsError):
            raise Conflict(f"destination appeared meanwhile: {dst}") from e
        _guard(e)
    copy(src, dst, sha)  # cross-volume: copy -> verify -> publish, then source to trash
    trash(src, sha)
    return {"dst_sha256": sha, "via": "copy+trash"}


def trash(path: Path, sha: str | None) -> dict[str, Any]:
    if not path.exists():
        raise Conflict(f"missing: {path}")
    if sha is not None and path.is_file() and sha256_file(path) != sha:
        raise Conflict(f"changed since recorded: {path}")
    try:
        sysinfo.move_to_trash(path)  # platform adapter (OS recycle bin / XDG trash)
    except OSError as e:
        _guard(e)
        raise
    return {"trashed": str(path)}


def mkdir(path: Path) -> dict[str, Any]:
    if path.is_dir():
        return {"existed": True}
    if path.exists():
        raise Conflict(f"a file exists where a folder is planned: {path}")
    path.mkdir()
    return {"existed": False}
