"""Host HTTP fetch service (implements contracts.http.HttpContext for plugins).

Only hosts granted to the calling plugin; https only; the resolved address must be public (SSRF guard, re-checked
on every redirect); per-host minimum interval; bounded retries honouring Retry-After; response size cap enforced
while streaming; per-instance TTL cache that never stores credentialed or `no-store`/`private` responses; credential
headers are dropped on cross-host redirects.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Callable, Mapping
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from ...contracts.http import HttpDenied, HttpPolicy, HttpResponse, HttpUnavailable

Resolver = Callable[[str], list[str]]
FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
# Host-owned politeness floors from the providers' published terms (a plugin policy can only be stricter).
HOST_MIN_INTERVAL_S = {"export.arxiv.org": 3.0}


def _resolve(host: str) -> list[str]:
    return [str(i[4][0]) for i in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


class HostHttp:
    # Test seams (process-wide): offline tests install an httpx.MockTransport and a fake resolver here.
    default_transport: httpx.BaseTransport | None = None
    default_resolver: Resolver | None = None
    allow_fake_ip: bool = False  # set from Settings.network_allow_fake_ip at startup (owner opt-in)
    # Politeness state is per host and process-wide on purpose (ADR-066); the response cache is per instance.
    _locks: dict[str, threading.Lock] = {}
    _last: dict[str, float] = {}

    def __init__(
        self,
        allowed_hosts: tuple[str, ...],
        policy: HttpPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.allowed = {h.lower() for h in allowed_hosts}
        self.policy = policy or HttpPolicy()
        self.client = httpx.Client(transport=transport or HostHttp.default_transport, timeout=self.policy.timeout_s,
                                   follow_redirects=False)
        self.resolver = resolver or HostHttp.default_resolver or _resolve
        self.clock, self.sleep = clock, sleep
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, HttpResponse]] = {}

    def _check(self, url: str) -> str:
        u = urlsplit(url)
        host = (u.hostname or "").lower()
        if u.scheme != "https":
            raise HttpDenied(f"only https is allowed: {url}")
        if host not in self.allowed:
            raise HttpDenied(f"host {host!r} is not granted to this plugin")
        for ip in self.resolver(host):
            a = ipaddress.ip_address(ip)
            if HostHttp.allow_fake_ip and a in FAKE_IP:
                continue  # proxy fake-IP: the proxy resolves and connects to the real host
            if a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast:
                hint = " (fake-IP proxy? see network_allow_fake_ip in docs/OPS.md)" if a in FAKE_IP else ""
                raise HttpDenied(f"{host} resolves to a non-public address{hint}")
        return host

    def _throttle(self, host: str) -> None:
        lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            gap = max(self.policy.min_interval_s, HOST_MIN_INTERVAL_S.get(host, 0.0))
            wait = self._last.get(host, -1e9) + gap - self.clock()
            if wait > 0:
                self.sleep(wait)
            self._last[host] = self.clock()

    def get(
        self, url: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> HttpResponse:
        full = url + ("?" + urlencode(sorted(params.items())) if params else "")
        sent = {**self.policy.extra_headers, **(headers or {})}
        cacheable = not any(_sensitive(h) for h in sent)
        key = (full, tuple(sorted((k.lower(), v) for k, v in sent.items())))
        hit = self._cache.get(key) if cacheable else None
        if hit and self.clock() - hit[0] < self.policy.cache_ttl_s:
            return HttpResponse(hit[1].status, hit[1].headers, hit[1].body, hit[1].url, from_cache=True)
        resp = self._fetch(full, sent)
        if cacheable and resp.status == 200 and _storable(resp.headers):
            self._cache[key] = (self.clock(), resp)
        return resp

    def _fetch(self, url: str, headers: Mapping[str, str]) -> HttpResponse:
        for attempt in range(self.policy.max_retries + 1):
            current, hdrs = url, dict(headers)
            for _hop in range(5):  # redirects re-validated hop by hop
                host = self._check(current)
                self._throttle(host)
                try:
                    status, rh, body = self._get_capped(current, hdrs)
                except httpx.HTTPError as e:
                    if attempt < self.policy.max_retries:
                        break
                    raise HttpUnavailable(f"network error: {type(e).__name__}") from e
                if status in (301, 302, 303, 307, 308) and "location" in rh:
                    nxt = urljoin(current, rh["location"])
                    if (urlsplit(nxt).hostname or "").lower() != host:  # never forward credentials to another host
                        hdrs = {k: v for k, v in hdrs.items() if not _sensitive(k)}
                    current = nxt
                    continue
                if status == 429 or status >= 500:
                    ra = rh.get("retry-after")
                    wait = float(ra) if ra and ra.isdigit() else None
                    if attempt < self.policy.max_retries:
                        self.sleep(min(wait or 2.0, 30.0))
                        break
                    raise HttpUnavailable(f"HTTP {status} from {host}", retry_after_s=wait)
                return HttpResponse(status, rh, body, current)
            else:
                raise HttpDenied("too many redirects")
        raise HttpUnavailable("request failed after retries")

    def _get_capped(self, url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        with self.client.stream("GET", url, headers=headers) as r:
            declared = r.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.policy.max_bytes:
                raise HttpDenied(f"response larger than {self.policy.max_bytes} bytes")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > self.policy.max_bytes:
                    raise HttpDenied(f"response larger than {self.policy.max_bytes} bytes")
            return r.status_code, {k.lower(): v for k, v in r.headers.items()}, bytes(buf)


_SENSITIVE = ("authorization", "cookie", "proxy-authorization")


def _sensitive(header: str) -> bool:
    """Credential-bearing request headers (Authorization, Cookie, X-Api-Key, Zotero-API-Key, *token*, *secret*)."""
    h = header.lower()
    return h in _SENSITIVE or any(w in h for w in ("key", "token", "secret", "auth"))


def _storable(headers: Mapping[str, str]) -> bool:
    cc = headers.get("cache-control", "").lower()
    return "no-store" not in cc and "private" not in cc and headers.get("vary", "").strip() != "*"
