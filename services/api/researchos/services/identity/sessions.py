"""Per-device sessions, pairing codes for extra devices (iPad), revocation, step-up re-auth."""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ...contracts.errors import ApiError
from ...kernel.db import Row, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from .credentials import check_password_for_user_id, check_user_password, sha256

PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
REAUTH_VALID_MIN = 15


@dataclass(frozen=True)
class Principal:
    user_id: str
    device_id: str
    session_id: str
    device_name: str
    is_host: bool
    reauth_at: str | None

    @property
    def actor(self) -> str:
        return f"device:{self.device_id}"

    def recently_reauthenticated(self) -> bool:
        if not self.reauth_at:
            return False
        return datetime.fromisoformat(self.reauth_at) + timedelta(minutes=REAUTH_VALID_MIN) > _now()


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _new_session(conn: sqlite3.Connection, user_id: str, device_id: str, ttl_hours: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    conn.execute("INSERT INTO sessions(id,token_hash,user_id,device_id,created_at,expires_at,last_seen_at) "
                 "VALUES (?,?,?,?,?,?,?)", (new_id("ses"), sha256(token), user_id, device_id, _iso(now),
                                             _iso(now + timedelta(hours=ttl_hours)), _iso(now)))
    return token


def _register_device(conn: sqlite3.Connection, user_id: str, name: str, via: str, is_host: bool,
                     user_agent: str | None, actor: str) -> str:
    did = new_id("dev")
    conn.execute("INSERT INTO devices(id,user_id,name,paired_via,is_host,user_agent,created_at,last_seen_at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (did, user_id, (name or "browser")[:80], via, int(is_host),
                                              (user_agent or "")[:300], utcnow(), utcnow()))
    emit(conn, "device.registered", actor=actor, entity_type="device", entity_id=did,
         data={"name": name[:80], "via": via, "is_host": is_host})
    return did


def login(conn: sqlite3.Connection, username: str, password: str, device_name: str, user_agent: str | None,
          ttl_hours: int, is_host: bool, via: str = "password", device_id: str | None = None) -> tuple[str, str]:
    """Re-uses the browser's existing (non-revoked) device row when its device cookie is presented."""
    uid = check_user_password(conn, username, password)
    with tx(conn):
        known = row(conn.execute("SELECT id FROM devices WHERE id=? AND user_id=? AND revoked_at IS NULL",
                                 (device_id, uid))) if device_id else None
        did = known["id"] if known else _register_device(conn, uid, device_name, via, is_host, user_agent,
                                                         f"user:{uid}")
        token = _new_session(conn, uid, did, ttl_hours)
        emit(conn, "auth.login", actor=f"device:{did}", entity_type="device", entity_id=did)
    return token, did


def resolve_session(conn: sqlite3.Connection, token: str | None, idle_hours: int) -> Principal | None:
    if not token:
        return None
    s = row(conn.execute(
        "SELECT s.*, d.name AS device_name, d.is_host, d.revoked_at AS device_revoked_at FROM sessions s "
        "JOIN devices d ON d.id=s.device_id WHERE s.token_hash=?", (sha256(token),)))
    if not s or s["revoked_at"] or s["device_revoked_at"]:
        return None
    now = _now()
    last = datetime.fromisoformat(s["last_seen_at"])
    if datetime.fromisoformat(s["expires_at"]) <= now or last + timedelta(hours=idle_hours) <= now:
        return None
    if last + timedelta(seconds=60) <= now:  # touch at most once a minute
        conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (_iso(now), s["id"]))
        conn.execute("UPDATE devices SET last_seen_at=? WHERE id=?", (_iso(now), s["device_id"]))
    return Principal(s["user_id"], s["device_id"], s["id"], s["device_name"], bool(s["is_host"]), s["reauth_at"])


def logout(conn: sqlite3.Connection, session_id: str) -> None:
    with tx(conn):
        conn.execute("UPDATE sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (utcnow(), session_id))


def reauthenticate(conn: sqlite3.Connection, p: Principal, password: str) -> str:
    """Step-up: password re-entry, required for remote approvals of sensitive subjects (K3)."""
    if not check_password_for_user_id(conn, p.user_id, password):
        raise ApiError(401, "invalid_credentials", "password incorrect")
    now = utcnow()
    with tx(conn):
        conn.execute("UPDATE sessions SET reauth_at=? WHERE id=?", (now, p.session_id))
        emit(conn, "auth.reauth", actor=p.actor, entity_type="device", entity_id=p.device_id)
    return now


def create_pairing_code(conn: sqlite3.Connection, by: Principal, device_name: str | None,
                        ttl_min: int) -> tuple[str, str]:
    code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    exp = _iso(_now() + timedelta(minutes=ttl_min))
    with tx(conn):
        pid = new_id("pair")
        conn.execute("INSERT INTO pairing_codes(id,code_hash,created_by_device,device_name,created_at,expires_at) "
                     "VALUES (?,?,?,?,?,?)", (pid, sha256(code), by.device_id, (device_name or "")[:80], utcnow(), exp))
        emit(conn, "device.pairing_code_created", actor=by.actor, entity_type="pairing", entity_id=pid)
    return code, exp


def redeem_pairing_code(conn: sqlite3.Connection, code: str, device_name: str, user_agent: str | None,
                        ttl_hours: int) -> tuple[str, str]:
    code = (code or "").strip().upper().replace("-", "").replace(" ", "")
    with tx(conn):
        p = row(conn.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (sha256(code),)))
        if not p or p["used_at"] or datetime.fromisoformat(p["expires_at"]) <= _now():
            raise ApiError(401, "invalid_pairing_code", "pairing code invalid, used or expired")
        creator = row(conn.execute("SELECT * FROM devices WHERE id=?", (p["created_by_device"],)))
        if not creator or creator["revoked_at"]:
            raise ApiError(401, "invalid_pairing_code", "issuing device was revoked")
        conn.execute("UPDATE pairing_codes SET used_at=? WHERE id=?", (utcnow(), p["id"]))
        name = device_name or p["device_name"] or "paired device"
        did = _register_device(conn, creator["user_id"], name, "pairing_code", False, user_agent,
                               f"device:{creator['id']}")
        token = _new_session(conn, creator["user_id"], did, ttl_hours)
    return token, did


def list_devices(conn: sqlite3.Connection, user_id: str) -> list[Row]:
    return rows(conn.execute("SELECT id,name,paired_via,is_host,created_at,last_seen_at,revoked_at FROM devices "
                             "WHERE user_id=? ORDER BY created_at", (user_id,)))


def revoke_device(conn: sqlite3.Connection, device_id: str, by: Principal) -> None:
    with tx(conn):
        d = row(conn.execute("SELECT * FROM devices WHERE id=? AND user_id=?", (device_id, by.user_id)))
        if not d:
            raise ApiError(404, "not_found", "device not found")
        if d["revoked_at"]:
            return
        now = utcnow()
        conn.execute("UPDATE devices SET revoked_at=? WHERE id=?", (now, device_id))
        conn.execute("UPDATE sessions SET revoked_at=? WHERE device_id=? AND revoked_at IS NULL", (now, device_id))
        emit(conn, "device.revoked", actor=by.actor, entity_type="device", entity_id=device_id)
