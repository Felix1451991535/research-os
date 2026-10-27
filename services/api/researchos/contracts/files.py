"""Read-only local file access for owner-initiated work (core 1.5). The host grants it only to plugins whose manifest
declares the capability `files:read`, and only inside owner-configured file roots with read access (canonical paths,
no symlink / junction crossing). There is no write counterpart: plugins write only into their own output directory."""

from __future__ import annotations

from typing import Protocol

FILES_READ = "files:read"


class LocalReadContext(Protocol):
    """Errors: PermissionError outside the roots, FileNotFoundError, ValueError when a limit is exceeded."""

    def list_files(self, directory: str, *, suffixes: tuple[str, ...] = (), limit: int = 10_000) -> list[str]:
        """Absolute paths of the regular files below `directory` (recursive, sorted; symlinks are skipped).
        `suffixes` filters case-insensitively (e.g. (".dcm",)); () = every file."""
        ...

    def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
        """Whole file content; ValueError if larger than `max_bytes`."""
        ...
