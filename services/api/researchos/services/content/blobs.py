"""K8 BlobStore: content-addressed originals under raw/ (temp -> verify -> atomic publish). Originals are
read-only once published; stored paths are relative to data_dir."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path


class BlobStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.root = data_dir / "raw"

    def rel_path(self, sha: str, ext: str) -> str:
        return f"raw/{sha[:2]}/{sha}{ext}"

    def put(self, data: bytes, ext: str) -> tuple[str, str]:
        """Returns (sha256, relative path). Idempotent: identical bytes map to the same file."""
        sha = hashlib.sha256(data).hexdigest()
        rel = self.rel_path(sha, ext)
        dest = self.data_dir / rel
        if dest.is_file():
            return sha, rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".partial")
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if hashlib.sha256(tmp.read_bytes()).hexdigest() != sha:  # verify before publish
            tmp.unlink()
            raise OSError("blob verification failed after write")
        os.replace(tmp, dest)
        dest.chmod(stat.S_IREAD | stat.S_IRGRP)  # originals are read-only
        return sha, rel

    def read(self, rel: str) -> bytes:
        return (self.data_dir / rel).read_bytes()
