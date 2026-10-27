"""Failed-attempt limiter keyed by client address + account (in-memory; resets on restart)."""
from __future__ import annotations

import ipaddress
import threading
import time

from ...contracts.errors import ApiError


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def client_key(peer: str | None, forwarded_for: str | None, trust_forwarded_for: bool) -> str:
    """Behind a local reverse proxy (e.g. `tailscale serve`) every client appears as 127.0.0.1. Only then, and only
    when explicitly enabled, the first X-Forwarded-For hop identifies the client."""
    if trust_forwarded_for and is_loopback(peer) and forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return peer or "unknown"


class FailureLimiter:
    def __init__(self, max_failures: int = 5, window_s: float = 300) -> None:
        self.max, self.window = max_failures, window_s
        self._fail: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            hits = [t for t in self._fail.get(key, []) if now - t < self.window]
            self._fail[key] = hits
            if len(hits) >= self.max:
                raise ApiError(429, "too_many_attempts", "too many failed attempts; wait a few minutes",
                               retryable=True)

    def fail(self, key: str) -> None:
        with self._lock:
            self._fail.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fail.pop(key, None)
