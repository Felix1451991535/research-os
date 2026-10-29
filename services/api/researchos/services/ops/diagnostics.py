"""Diagnostics bundle (ADR-230): one zip the owner can attach to a bug report. Only the ALLOWED entries are written:
the setup / launcher / update logs (tails), version, OS facts and the last task errors, every text redacted first:
API keys by the log redaction (kernel.logsetup.redact), then the data / install / home paths, user names inside
user-profile paths and e-mail addresses. Nothing is sent anywhere; the web downloads the zip and the owner checks it."""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import platform
import re
import sqlite3
import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psutil

from ... import __version__
from ...kernel.config import REPO_ROOT, Settings
from ...kernel.logsetup import redact
from .updater import install_dir

MAX_LOG_BYTES = 4 * 1024 * 1024
LOG_TAIL_LINES = 3000
TASK_ERRORS = 200
# zip entry -> where it comes from ("install" = the setup-made install dir, "logs" = <data>/logs)
LOGS = {"logs/install.log": ("install", "install.log"), "logs/install.prev.log": ("install", "install.prev.log"),
        "logs/launcher.log": ("logs", "launcher.log"), "logs/update.log": ("logs", "update.log")}
ALLOWED = frozenset({*LOGS, "version.json", "system.json", "task_errors.json", "README.txt"})
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_SEP = r"[\\/]+"
_USER_DIR = re.compile(r"(?i)(\b[A-Z]:[\\/]+(?:Users|Documents and Settings)[\\/]+|/home/|/Users/)[^\\/\s\"':;]+")
README = """Research OS diagnostics bundle (version {version}, created {created} UTC).
Contents: {entries}.
Redacted before writing: API keys and tokens, the data / install / home folder paths (shown as <data>, <install>,
<home>), user names in user-profile paths (<user>) and e-mail addresses (<email>). Your documents, notes, library
and settings are NOT included. Please look through the files before you attach the zip to a report.
"""


class Redactor:
    """Secrets (existing log redaction) + this install's folders + user-profile names + e-mail addresses."""

    def __init__(self, places: dict[str, Path]) -> None:
        pairs = []
        for label, path in places.items():
            text = str(path)
            parts = [p for p in re.split(_SEP, text) if p]
            if len(text) > 3 and parts:  # never a bare drive root
                # any run of / or \ between the parts: both separators, and backslashes already escaped once or
                # more inside an exception's repr or JSON text (Review #97)
                lead = _SEP if text[0] in "\\/" else ""
                pairs.append((len(text), lead + _SEP.join(map(re.escape, parts)), f"<{label}>"))
        pairs.sort(key=lambda p: -p[0])  # the longest (most specific) path first
        self._paths = [(re.compile(rx, re.I), rep) for _n, rx, rep in pairs]

    def __call__(self, text: str) -> str:
        text = redact(text)
        for rx, rep in self._paths:
            text = rx.sub(rep, text)
        text = _USER_DIR.sub(lambda m: m.group(1) + "<user>", text)
        return _EMAIL.sub("<email>", text)

    def tree(self, data: Any) -> Any:
        """Every string (keys too) of JSON-able data redacted BEFORE serialisation: json.dumps escapes backslashes
        and quotes, so a Windows path or a secret in its serialised form no longer matches (Review #89)."""
        if isinstance(data, str):
            return self(data)
        if isinstance(data, dict):
            return {self(str(k)): self.tree(v) for k, v in data.items()}
        if isinstance(data, (list, tuple)):
            return [self.tree(v) for v in data]
        return data


def _decode(raw: bytes, utf16: bool) -> str:
    if utf16:  # the NSIS install.log is UTF-16LE with BOM (ADR-212)
        return raw.decode("utf-16-le", "replace").lstrip("﻿")
    return raw.decode("utf-8", "replace").lstrip("﻿")


def read_tail(path: Path) -> str | None:
    """The last LOG_TAIL_LINES lines (from at most MAX_LOG_BYTES), or None if the file does not exist."""
    try:
        with path.open("rb") as fh:
            utf16 = fh.read(2) == b"\xff\xfe"
            size = fh.seek(0, os.SEEK_END)
            start = max(0, size - MAX_LOG_BYTES)
            fh.seek(start - start % 2)  # stay on a UTF-16 code unit boundary
            raw = fh.read()
    except FileNotFoundError:
        return None
    return "\n".join(_decode(raw, utf16).splitlines()[-LOG_TAIL_LINES:]) + "\n"


def system_facts(settings: Settings, installed: bool) -> dict[str, Any]:
    """OS / runtime facts that help to reproduce a problem; no host name, no user name."""
    try:
        free = psutil.disk_usage(str(settings.data_dir)).free
    except OSError:
        free = None
    return {"os": platform.platform(), "machine": platform.machine(), "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(), "memory_bytes": psutil.virtual_memory().total, "data_disk_free_bytes": free,
            "installed_copy": installed, "tls": settings.tls_enabled,
            "dev_mode": settings.dev_mode}


def task_errors(conn: sqlite3.Connection, limit: int = TASK_ERRORS) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, kind, state, stage, attempt, error_code, error_message, updated_at FROM tasks "
        "WHERE error_code IS NOT NULL OR state IN ('failed', 'interrupted', 'blocked') "
        "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    cols = ("id", "kind", "state", "stage", "attempt", "error_code", "error_message", "updated_at")
    return [dict(zip(cols, tuple(r), strict=True)) for r in rows]


def _sources(settings: Settings, app_root: Path) -> dict[str, Path | None]:
    base: dict[str, Path | None] = {"install": install_dir(app_root), "logs": settings.logs_dir}
    out: dict[str, Path | None] = {}
    for entry, (where, name) in LOGS.items():
        folder = base[where]
        out[entry] = folder / name if folder is not None else None
    return out


def bundle_name(now: dt.datetime) -> str:
    return f"ResearchOS-diagnostics-{__version__}-{now:%Y%m%d-%H%M%S}.zip"


def build_bundle(settings: Settings, conn: sqlite3.Connection, *, app_root: Path = REPO_ROOT,
                 now: dt.datetime | None = None) -> tuple[str, bytes]:
    """(file name, zip bytes); every entry is in ALLOWED and redacted."""
    now = now or dt.datetime.now(dt.UTC)
    places = {"data": settings.data_dir, "home": Path.home()}
    inst = install_dir(app_root)
    if inst is not None:
        places["install"] = inst
    red = Redactor(places)
    entries: dict[str, str] = {}
    for entry, path in _sources(settings, app_root).items():
        text = read_tail(path) if path else None
        if text is not None:
            entries[entry] = red(text)
    entries["version.json"] = _json({"version": __version__, "created_utc": now.isoformat(timespec="seconds")})
    entries["system.json"] = _json(red.tree(system_facts(settings, inst is not None)))
    entries["task_errors.json"] = _json(red.tree(task_errors(conn)))
    entries["README.txt"] = README.format(version=__version__, created=f"{now:%Y-%m-%d %H:%M}",
                                          entries=", ".join(sorted(entries)))
    return bundle_name(now), _zip(entries.items())


def _json(data: Any) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def _zip(entries: Iterable[tuple[str, str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in entries:
            if name not in ALLOWED:
                raise ValueError(f"{name} is not an allow-listed diagnostics entry")
            z.writestr(name, text)
    return buf.getvalue()
