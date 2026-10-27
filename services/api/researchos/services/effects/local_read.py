"""Host implementation of contracts.files.LocalReadContext: every path goes through roots.resolve(read), so plugin
reads obey the same owner file roots, hard-deny list and symlink/junction refusal as file effects (ADR-155).

Containment review (ADR-163): the path check and the read are one step - on POSIX the file is opened component by
component from the root's directory handle with O_NOFOLLOW (a component swapped for a symlink after the check fails
to open); on Windows the opened handle is re-checked against the resolved path. Hard-linked files (st_nlink > 1) are
refused because a link inside a root can name a file outside it; only regular files are read, at most max_bytes.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ...contracts.errors import ApiError
from ...kernel.db import connect
from ...platform import IS_WINDOWS
from ...platform.paths import is_within
from . import roots


class RootsReader:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _resolve(self, raw: str) -> tuple[Path, Path]:
        """(resolved path, the read root containing it)."""
        conn = connect(self.db_path)
        try:
            p = roots.resolve(conn, raw, "read", must_exist=True)
            root = next(Path(r["path"]) for r in roots.get_roots(conn) if is_within(Path(r["path"]), p))
            return p, root
        except ApiError as e:
            raise (FileNotFoundError if e.code == "missing" else PermissionError)(e.message) from None
        finally:
            conn.close()

    def list_files(self, directory: str, *, suffixes: tuple[str, ...] = (), limit: int = 10_000) -> list[str]:
        base, _ = self._resolve(directory)
        if not base.is_dir():
            raise NotADirectoryError(str(base))
        wanted = tuple(s.lower() for s in suffixes)
        out: list[str] = []
        for top, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not _link(Path(top) / d))  # junctions too: names stay inside
            for name in sorted(files):
                p = Path(top) / name
                if _link(p) or not p.is_file() or (wanted and not name.lower().endswith(wanted)):
                    continue
                out.append(str(p))
                if len(out) > limit:
                    raise ValueError(f"more than {limit} files below {base}")
        return sorted(out)

    def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
        p, root = self._resolve(path)
        fd = _open_contained(root, p)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise FileNotFoundError(f"{p} is not a regular file")
            if st.st_nlink > 1:
                raise PermissionError(f"{p} has {st.st_nlink} hard links; a link can name a file outside the root")
            with os.fdopen(fd, "rb", closefd=False) as f:
                data = f.read(max_bytes + 1)  # the file may grow after fstat: never read past the limit
        finally:
            os.close(fd)
        if len(data) > max_bytes:
            raise ValueError(f"{p} is larger than {max_bytes} bytes")
        return data


def _link(p: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", None)
    return p.is_symlink() or bool(is_junction and is_junction(p))


def _open_contained(root: Path, p: Path) -> int:
    """Open `p` for reading so that no component can be swapped for a link between the check and the open."""
    if IS_WINDOWS:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        real = Path(os.path.realpath(p))
        if not is_within(root, real) or not os.path.samestat(os.fstat(fd), os.stat(real)):
            os.close(fd)
            raise PermissionError(f"{p} changed or left the root while being opened")
        return fd
    parts = p.relative_to(root).parts
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            flags = os.O_RDONLY | os.O_NOFOLLOW | (0 if last else os.O_DIRECTORY)
            try:
                nxt = os.open(part, flags, dir_fd=fd)
            except OSError as e:  # ELOOP / ENOTDIR: a component became a symlink or changed type
                raise PermissionError(f"{p} changed while being opened ({e.strerror})") from None
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd
