"""Request-scoped dependencies: services container, per-request DB connection, principal, idempotency."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Depends, Request

from ..contracts.errors import ApiError
from ..kernel import idempotency as idem
from ..kernel.db import connect, jdump, tx
from ..services.container import Services
from ..services.identity.ratelimit import client_key, is_loopback
from ..services.identity.sessions import Principal, resolve_session

SESSION_COOKIE = "ros_session"


def services(request: Request) -> Services:
    s: Services = request.app.state.services
    return s


def db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(services(request).settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _token(request: Request) -> str | None:
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        return tok
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


def optional_principal(request: Request, conn: sqlite3.Connection = Depends(db)) -> Principal | None:
    return resolve_session(conn, _token(request), services(request).settings.session_idle_hours)


def principal(p: Principal | None = Depends(optional_principal)) -> Principal:
    if p is None:
        raise ApiError(401, "unauthenticated", "login required")
    return p


def cid(request: Request) -> str:
    return str(getattr(request.state, "correlation_id", ""))


def peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def peer_is_host(request: Request) -> bool:
    return is_loopback(peer(request)) and "x-forwarded-for" not in request.headers


def limiter_key(request: Request) -> str:
    return client_key(peer(request), request.headers.get("x-forwarded-for"),
                      services(request).settings.trust_forwarded_for)


def idempotent(request: Request, conn: sqlite3.Connection, scope: str, payload: Any,
               fn: Callable[[], tuple[int, Any]]) -> tuple[int, Any]:
    """Run fn inside one IMMEDIATE transaction keyed by the Idempotency-Key header; replay stored responses.
    Pattern for actions that also write files: write the file to a temp path BEFORE calling this, publish (rename)
    inside fn, and make fn tolerate an already-published file (content-addressed names)."""
    key = request.headers.get("idempotency-key")
    if not key:
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key header required for this write")
    h = idem.request_hash(payload)
    with tx(conn):
        prior = idem.lookup(conn, scope, key, h)
        if prior:
            return prior
        status, body = fn()
        idem.store(conn, scope, key, h, status, body)
    return status, body


def idempotent_long(request: Request, conn: sqlite3.Connection, scope: str, payload: Any,
                    fn: Callable[..., tuple[int, Any]], *, resumable: bool = False) -> tuple[int, Any]:
    """For long actions that manage their own transactions (e.g. paid AI calls): replay if already done, else run
    and record. A concurrent duplicate that loses the insert race gets the first stored response."""
    if resumable:
        return _resume(request, conn, scope, payload, fn)
    key = request.headers.get("idempotency-key")
    if not key:
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key header required for this write")
    h = idem.request_hash(payload)
    prior = idem.lookup(conn, scope, key, h)
    if prior:
        return prior
    status, body = fn()
    try:
        with tx(conn):
            idem.store(conn, scope, key, h, status, body)
    except sqlite3.IntegrityError:
        stored = idem.lookup(conn, scope, key, h)
        if stored:
            return stored
    return status, body


def _resume(request: Request, conn: sqlite3.Connection, scope: str, payload: Any,
            fn: Callable[..., tuple[int, Any]]) -> tuple[int, Any]:
    from ..kernel.idempotency import operation_lock

    key = request.headers.get("idempotency-key")
    if not key:
        raise ApiError(400, "idempotency_key_required", "Idempotency-Key header required for this write")
    h = idem.request_hash(payload)
    lock = services(request).settings.data_dir / "operation-locks" / idem.request_hash([scope, key])
    with operation_lock(lock):
        with tx(conn):
            prior = idem.lookup(conn, scope, key, h)
            if prior and prior[0] != 102:
                return prior
            progress = prior[1] if prior else {}
            if not prior:
                idem.store(conn, scope, key, h, 102, progress)

        def checkpoint() -> None:
            with tx(conn, join=True):
                idem.checkpoint(conn, scope, key, progress)

        status, body = fn(progress, checkpoint)
        with tx(conn):
            conn.execute("UPDATE idempotency SET status_code=?,response_json=? WHERE scope=? AND key=?",
                         (status, jdump(body), scope, key))
        return status, body
