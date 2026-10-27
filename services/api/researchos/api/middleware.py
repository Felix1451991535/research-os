"""Pure ASGI security middleware (no BaseHTTPMiddleware: safe for streaming/SSE).

Correlation ids, Host allowlist (DNS-rebinding defence), Origin check + custom header on state-changing API calls
(CSRF defence), security headers.
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..kernel.config import Settings
from .errors import error_body

CSRF_HEADER = "x-researchos-client"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
JSON_BODY_LIMIT = 2 * 1024 * 1024
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'")


def parse_host(value: str) -> str:
    """Host header -> hostname. Handles 'h', 'h:port', '[v6]', '[v6]:port' and bare IPv6 literals."""
    v = value.strip().lower()
    if v.startswith("["):
        return v[1:v.index("]")] if "]" in v else v[1:]
    if v.count(":") > 1:  # bare IPv6 without brackets/port
        return v
    return v.rsplit(":", 1)[0] if ":" in v else v


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        hosts = {"127.0.0.1", "localhost", "::1", *(h.lower() for h in settings.extra_allowed_hosts)}
        for o in settings.allowed_origins():
            h = urlsplit(o).hostname
            if h:
                hosts.add(h.lower())
        self.hosts = hosts
        self.origins = settings.allowed_origins()

    async def _reject(self, send: Send, status: int, code: str, msg: str, cid: str) -> None:
        body = json.dumps(error_body(code, msg, False, cid)).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"x-correlation-id", cid.encode())]})
        await send({"type": "http.response.body", "body": body})

    def _violation(self, scope: Scope, headers: dict[str, str]) -> tuple[int, str, str] | None:
        too_big = self._oversized(scope, headers)
        if too_big:
            return too_big
        host = parse_host(headers.get("host", ""))
        if host and host not in self.hosts:
            return 421, "bad_host", f"Host '{host}' not allowed; add its URL to public_origins"
        if str(scope.get("path", "")).startswith("/api/") and scope.get("method") not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin and origin.rstrip("/") not in self.origins:
                return 403, "bad_origin", f"Origin '{origin}' not allowed"
            if CSRF_HEADER not in headers:
                return 403, "missing_client_header", f"header {CSRF_HEADER} required"
        return None

    def _oversized(self, scope: Scope, headers: dict[str, str]) -> tuple[int, str, str] | None:
        """Request preflight (HARDENING #16): refuse oversized bodies from Content-Length before reading them.
        Uploads may use max_upload_mb; every other API body is capped at 2 MB. Chunked bodies without a length
        are refused on API writes."""
        if scope.get("method") in SAFE_METHODS:
            return None
        path = str(scope.get("path", ""))
        limit = self.settings.max_upload_mb * 1024 * 1024 if path.endswith("/upload") else JSON_BODY_LIMIT
        raw = headers.get("content-length")
        if raw is None:
            if path.startswith("/api/") and headers.get("transfer-encoding"):
                return 411, "length_required", "Content-Length required"
            return None
        try:
            n = int(raw)
        except ValueError:
            return 400, "bad_content_length", "invalid Content-Length"
        if n > limit:
            return 413, "payload_too_large", f"request body {n} bytes exceeds limit {limit}"
        return None

    def _extra_headers(self, path: str, existing: set[bytes], cid: str) -> list[tuple[bytes, bytes]]:
        extra = [(b"x-correlation-id", cid.encode()), (b"x-content-type-options", b"nosniff"),
                 (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY")]
        if b"content-security-policy" not in existing:
            extra.append((b"content-security-policy", CSP.encode()))
        if self.settings.tls_enabled:
            extra.append((b"strict-transport-security", b"max-age=31536000"))
        if path.startswith("/api/") and b"cache-control" not in existing:
            extra.append((b"cache-control", b"no-store"))
        return extra

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        cid = (headers.get("x-correlation-id") or uuid.uuid4().hex[:16])[:64]
        scope.setdefault("state", {})["correlation_id"] = cid
        bad = self._violation(scope, headers)
        if bad:
            await self._reject(send, bad[0], bad[1], bad[2], cid)
            return
        path = str(scope.get("path", ""))

        async def send_wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                hs: list[Any] = list(message.get("headers", []))
                message["headers"] = hs + self._extra_headers(path, {k.lower() for k, _ in hs}, cid)
            await send(message)

        await self.app(scope, receive, send_wrapped)
