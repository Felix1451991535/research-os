"""Owner-configured file roots (ARCHITECTURE §7): allowlist of {path, access read|write, cloud_allowed}; default
empty = the file agent can touch nothing. Hard-denied locations can never become roots. Every path an op uses is
canonicalized, checked component by component (Windows rules everywhere) and refused across reparse points."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from ...contracts.errors import ApiError
from ...kernel.db import jdump, jload, row, tx, utcnow
from ...kernel.events import emit
from ...platform import IS_WINDOWS
from ...platform.paths import PathRuleError, check_component, has_reparse_point, is_within, reject_dangerous_syntax

KEY = "file_roots"


def hard_denied(data_dir: Path) -> list[Path]:
    home = Path.home()
    deny = [
        home / ".ssh",
        home / ".gnupg",
        home / ".mozilla",
        home / ".config" / "google-chrome",
        home / ".config" / "chromium",
        home / "AppData" / "Local" / "Google" / "Chrome",
        home / "AppData" / "Roaming" / "Mozilla",
        data_dir / "db",
        data_dir / "config",
    ]
    if IS_WINDOWS:
        sysroot = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        deny += [
            sysroot,
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        ]
    else:
        deny += [Path(p) for p in ("/etc", "/bin", "/sbin", "/usr", "/boot", "/proc", "/sys", "/dev", "/var/lib")]
    return deny


def get_roots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    r = row(conn.execute("SELECT value_json FROM settings_kv WHERE key=?", (KEY,)))
    return list(jload(r["value_json"]) or []) if r else []


def set_roots(
    conn: sqlite3.Connection, data_dir: Path, roots: list[dict[str, Any]], actor: str
) -> list[dict[str, Any]]:
    """Owner action only (no agent route). Each root must be an existing absolute directory outside hard-deny."""
    clean = []
    for r in roots:
        raw = str(r.get("path") or "")
        try:
            reject_dangerous_syntax(raw)
        except PathRuleError as e:
            raise ApiError(422, "bad_root", str(e)) from None
        p = Path(raw)
        if not p.is_absolute() or not p.is_dir():
            raise ApiError(422, "bad_root", f"{raw!r} is not an existing absolute directory")
        rp = p.resolve()
        if rp == Path(rp.anchor) or any(
            rp == d.resolve() or is_within(d, rp) or is_within(rp, d) for d in hard_denied(data_dir) if d.exists()
        ):
            raise ApiError(422, "root_denied", f"{raw!r} is a protected location")
        if r.get("access") not in ("read", "write"):
            raise ApiError(422, "bad_root", "access must be read or write")
        clean.append({"path": str(rp), "access": r["access"], "cloud_allowed": bool(r.get("cloud_allowed", False))})
    with tx(conn):
        conn.execute(
            "INSERT INTO settings_kv(key,value_json,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
            "SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (KEY, jdump(clean), utcnow()),
        )
        emit(
            conn,
            "settings.file_roots_changed",
            actor=actor,
            entity_type="settings",
            entity_id=KEY,
            data={"roots": clean},
        )
    return clean


def resolve(conn: sqlite3.Connection, raw: str, need: str, *, must_exist: bool) -> Path:
    """Canonical path inside a root granting `need` (read | write); raises ApiError(403/422) otherwise."""
    try:
        reject_dangerous_syntax(raw)
    except PathRuleError as e:
        raise ApiError(422, "bad_path", str(e)) from None
    p = Path(raw)
    if not p.is_absolute():
        raise ApiError(422, "bad_path", f"absolute path required: {raw!r}")
    for root in get_roots(conn):
        rp = Path(root["path"])
        if (need == "read" or root["access"] == "write") and is_within(rp, p):
            if has_reparse_point(p, rp):
                raise ApiError(403, "reparse_point", f"{raw!r} crosses a symlink/junction")
            if not must_exist:  # names we CREATE must be valid on Windows too; existing sources are what they are
                try:
                    for comp in p.resolve().relative_to(rp.resolve()).parts:
                        check_component(comp)
                except PathRuleError as e:
                    raise ApiError(422, "bad_path", str(e)) from None
            if must_exist and not p.exists():
                raise ApiError(409, "missing", f"{raw!r} does not exist")
            return p.resolve()
    raise ApiError(403, "outside_roots", f"{raw!r} is not inside a {need} root configured by the owner")
