"""Path rules shared by the file agent (K4) and storage (K1). Pure functions; `windows` flag is explicit so
the Windows rules are unit-tested on Linux too."""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import IS_WINDOWS

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_INVALID_WIN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
LONG_PATH_LIMIT = 240  # leave headroom below MAX_PATH=260


class PathRuleError(ValueError):
    pass


def check_component(name: str) -> None:
    """Reject names illegal on Windows. Applied on every OS: a library created on Linux must stay valid on
    Windows."""
    if name in ("", ".", ".."):
        raise PathRuleError(f"invalid path component {name!r}")
    if _INVALID_WIN.search(name):
        raise PathRuleError(f"character not allowed on Windows in {name!r}")
    if name.endswith((".", " ")):
        raise PathRuleError(f"trailing dot/space not allowed on Windows: {name!r}")
    if name.split(".", 1)[0].upper() in _RESERVED:
        raise PathRuleError(f"reserved device name on Windows: {name!r}")


def compare_key(path: str, windows: bool = IS_WINDOWS) -> str:
    """Key for equality/collision checks: NFC normalized; case-folded on Windows (case-insensitive FS)."""
    p = unicodedata.normalize("NFC", path)
    if windows:
        return str(PureWindowsPath(p)).casefold()
    return str(PurePosixPath(p))


def reject_dangerous_syntax(raw: str, windows: bool = IS_WINDOWS) -> None:
    """Syntax that must never reach the filesystem layer."""
    if windows:
        pw = PureWindowsPath(raw)
        if pw.drive and not pw.root:
            raise PathRuleError(f"drive-relative path not allowed: {raw!r}")
        if raw.startswith(("\\\\", "//")):
            raise PathRuleError(f"UNC/network path not allowed by default: {raw!r}")
        tail = raw[len(pw.drive):]
        if ":" in tail:
            raise PathRuleError(f"alternate data stream syntax not allowed: {raw!r}")


def long_path(p: Path) -> str:
    """Return a string usable by Win32 APIs for paths beyond MAX_PATH."""
    s = str(p)
    if IS_WINDOWS and len(s) >= LONG_PATH_LIMIT and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


def has_reparse_point(path: Path, stop_at: Path) -> bool:
    """True if any existing component between stop_at (exclusive) and path is a symlink/junction/mount point."""
    cur = path
    stop = stop_at.resolve()
    while True:
        if (cur.exists() or cur.is_symlink()) and (cur.is_symlink() or _is_junction(cur)):
            return True
        if cur.resolve() == stop or cur.parent == cur:
            return False
        cur = cur.parent


def _is_junction(p: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction(p)) if is_junction else False


def is_within(root: Path, candidate: Path) -> bool:
    """Containment after full resolution (prefix string checks are insufficient: baseline §7)."""
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (ValueError, FileNotFoundError):
        return False


def to_stored(data_dir: Path, p: Path) -> str:
    """Stored paths are relative to data_dir in POSIX form (K1 invariant)."""
    return p.resolve().relative_to(data_dir.resolve()).as_posix()


def from_stored(data_dir: Path, rel: str) -> Path:
    if rel.startswith("/") or ".." in PurePosixPath(rel).parts:
        raise PathRuleError(f"stored path escapes data dir: {rel!r}")
    return data_dir / PurePosixPath(rel)
