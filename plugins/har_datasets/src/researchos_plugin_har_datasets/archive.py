"""Bounded in-memory ZIP/tar reading; never extracts arbitrary archive paths."""

from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from pathlib import PurePosixPath

LIMIT = 256 * 1024 * 1024


def safe_name(name: str) -> None:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("Unsafe archive member")


def read_archive(data: bytes, limit: int = LIMIT) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > 10000 or sum(i.file_size for i in infos) > limit:
                raise ValueError("Archive expansion exceeds limit")
            for info in infos:
                safe_name(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    raise ValueError("Symlink/encrypted archive member")
                if not info.is_dir():
                    if info.filename in out:
                        raise ValueError("Duplicate archive member")
                    out[info.filename] = archive.read(info)
    else:
        return read_tar(data, limit)
    return out


def member(files: dict[str, bytes], suffix: str) -> bytes:
    candidates = [v for k, v in files.items() if k == suffix or k.endswith("/" + suffix)]
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one archive member: {suffix}")
    return candidates[0]


def read_tar(data: bytes, limit: int) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        size = 0
        for index, info in enumerate(archive):
            safe_name(info.name)
            size += info.size
            if index >= 10000 or size > limit or not (info.isfile() or info.isdir()):
                raise ValueError("Unsafe tar member or expansion limit")
            if info.isfile():
                stream = archive.extractfile(info)
                if stream is None or info.name in out:
                    raise ValueError("Missing or duplicate archive member")
                out[info.name] = stream.read()
    return out
