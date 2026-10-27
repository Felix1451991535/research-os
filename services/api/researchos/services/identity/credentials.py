"""Local admin credentials and the one-time first-run setup token."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from pathlib import Path

import bcrypt

from ...contracts.errors import ApiError
from ...kernel.db import new_id, row, tx, utcnow
from ...kernel.events import emit

# A real bcrypt hash of a random throwaway password: verifying against it costs the same as a real check, so
# unknown usernames are not distinguishable by timing.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt(rounds=12)).decode("ascii")
MIN_PASSWORD_LEN = 10


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(pw: str, hashed: str | None) -> bool:
    try:
        ok = bcrypt.checkpw(pw.encode("utf-8"), (hashed or _DUMMY_HASH).encode("ascii"))
    except ValueError:
        return False
    return ok and hashed is not None


def validate_password_strength(pw: str) -> None:
    if len(pw) < MIN_PASSWORD_LEN:
        raise ApiError(422, "weak_password", f"password must be at least {MIN_PASSWORD_LEN} characters")


def admin_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def create_admin(conn: sqlite3.Connection, username: str, password: str) -> str:
    validate_password_strength(password)
    with tx(conn):
        if admin_exists(conn):
            raise ApiError(409, "already_initialized", "admin already exists")
        uid = new_id("usr")
        conn.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES (?,?,?,?)",
                     (uid, username, hash_password(password), utcnow()))
        emit(conn, "auth.admin_created", entity_type="user", entity_id=uid)
    return uid


def check_user_password(conn: sqlite3.Connection, username: str, password: str) -> str:
    u = row(conn.execute("SELECT id, password_hash FROM users WHERE username=?", (username,)))
    if not verify_password(password, u["password_hash"] if u else None) or not u:
        raise ApiError(401, "invalid_credentials", "invalid username or password")
    return str(u["id"])


def check_password_for_user_id(conn: sqlite3.Connection, user_id: str, password: str) -> bool:
    u = row(conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)))
    return verify_password(password, u["password_hash"] if u else None)


def reset_admin_password(conn: sqlite3.Connection, username: str, password: str) -> None:
    """Local CLI recovery path. Revokes every session."""
    validate_password_strength(password)
    with tx(conn):
        r = row(conn.execute("SELECT id FROM users WHERE username=?", (username,)))
        if not r:
            raise ApiError(404, "not_found", "user not found")
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), r["id"]))
        conn.execute("UPDATE sessions SET revoked_at=? WHERE revoked_at IS NULL", (utcnow(),))
        emit(conn, "auth.password_reset", entity_type="user", entity_id=r["id"])


# ---- first-run setup token (printed by start scripts; loopback check alone is not enough behind a local proxy)

def _token_path(config_dir: Path) -> Path:
    return config_dir / "setup-token.txt"


def ensure_setup_token(conn: sqlite3.Connection, config_dir: Path) -> str | None:
    path = _token_path(config_dir)
    if admin_exists(conn):
        path.unlink(missing_ok=True)
        return None
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(12)
    path.write_text(token + "\n", encoding="utf-8")
    return token


def check_setup_token(config_dir: Path, token: str) -> bool:
    path = _token_path(config_dir)
    if not path.is_file():
        return False
    return secrets.compare_digest(path.read_text(encoding="utf-8").strip(), (token or "").strip())


def clear_setup_token(config_dir: Path) -> None:
    _token_path(config_dir).unlink(missing_ok=True)
