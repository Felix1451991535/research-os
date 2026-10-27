"""Plugin-facing HTTP contract (K7 HTTP fetch service). Plugins never open sockets themselves: the host gives them
an HttpContext limited to the hosts their manifest declares (capability `network:<host>`), with timeouts, response
size caps, per-host rate limits, Retry-After handling, SSRF protection (no private/loopback targets, redirects
re-validated) and an optional response cache. Secrets are injected by the host as headers; plugins never see them."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str
    from_cache: bool = False

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpDenied(Exception):
    """Host refused the request (host not granted, private address, size cap). Never retried."""


class HttpUnavailable(Exception):
    """Transient failure after the host's bounded retries (timeout, 429/5xx). `retry_after_s` if known."""

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class HttpPolicy:
    timeout_s: float = 20.0
    max_bytes: int = 5_000_000
    min_interval_s: float = 1.0       # per host
    cache_ttl_s: float = 3600.0
    max_retries: int = 1
    extra_headers: Mapping[str, str] = field(default_factory=dict)


class HttpContext(Protocol):
    def get(self, url: str, *, params: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None) -> HttpResponse: ...
