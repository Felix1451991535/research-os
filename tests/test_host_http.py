"""HostHttp security properties (owner fix, batch 2 review): no shared or credentialed caching, credentials never
forwarded across hosts, size cap while streaming. Offline (httpx.MockTransport, fake resolver)."""

from __future__ import annotations

import httpx
import pytest

from researchos.contracts.http import HttpDenied, HttpPolicy
from researchos.services.knowledge.http_fetch import HostHttp

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def make(handler, hosts=("api.example.org",), **policy) -> HostHttp:  # type: ignore[no-untyped-def]
    return HostHttp(
        hosts,
        HttpPolicy(min_interval_s=0, **policy),
        transport=httpx.MockTransport(handler),
        resolver=PUBLIC,
        sleep=lambda s: None,
    )


def test_credentialed_responses_are_never_cached_or_shared() -> None:
    calls: list[str | None] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.headers.get("zotero-api-key"))
        return httpx.Response(200, text=f"library of {req.headers.get('zotero-api-key')}")

    a = make(handler)
    assert a.get("https://api.example.org/items", headers={"Zotero-API-Key": "K1"}).text() == "library of K1"
    second = a.get("https://api.example.org/items", headers={"Zotero-API-Key": "K2"})
    assert second.text() == "library of K2" and not second.from_cache
    assert not a.get("https://api.example.org/items", headers={"Zotero-API-Key": "K1"}).from_cache
    assert calls == ["K1", "K2", "K1"]


def test_cache_is_per_instance_and_header_aware() -> None:
    n = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        return httpx.Response(200, text=req.headers.get("accept", ""))

    a, b = make(handler), make(handler)
    a.get("https://api.example.org/x")
    assert a.get("https://api.example.org/x").from_cache
    assert not b.get("https://api.example.org/x").from_cache  # another instance never sees a's cache
    assert not a.get("https://api.example.org/x", headers={"Accept": "text/xml"}).from_cache
    assert n["calls"] == 3


def test_no_store_and_private_responses_not_cached() -> None:
    a = make(lambda req: httpx.Response(200, text="x", headers={"cache-control": "private, max-age=60"}))
    a.get("https://api.example.org/x")
    assert not a.get("https://api.example.org/x").from_cache


def test_credentials_dropped_on_cross_host_redirect() -> None:
    seen: dict[str, dict[str, str]] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen[req.url.host] = dict(req.headers)
        if req.url.host == "api.example.org":
            return httpx.Response(302, headers={"location": "https://cdn.example.net/file"})
        return httpx.Response(200, text="ok")

    a = make(handler, hosts=("api.example.org", "cdn.example.net"))
    r = a.get(
        "https://api.example.org/file",
        headers={"Authorization": "Bearer SECRET", "X-Api-Key": "K", "Accept": "application/json"},
    )
    assert r.status == 200 and r.url == "https://cdn.example.net/file"
    assert seen["api.example.org"]["authorization"] == "Bearer SECRET"
    hop = seen["cdn.example.net"]
    assert "authorization" not in hop and "x-api-key" not in hop and hop["accept"] == "application/json"


def test_size_cap_enforced_while_streaming() -> None:
    big = make(lambda req: httpx.Response(200, content=b"x" * 2048), max_bytes=1024)
    with pytest.raises(HttpDenied):
        big.get("https://api.example.org/big")
    declared = make(
        lambda req: httpx.Response(200, content=b"x" * 10, headers={"content-length": "999999"}), max_bytes=1024
    )
    with pytest.raises(HttpDenied):
        declared.get("https://api.example.org/declared")


def test_range_requests_are_forwarded_and_206_returned() -> None:
    """Documented contract for chunked dataset downloads (physio_signals): Range is forwarded; 206 is returned;
    different ranges never collide in the cache."""
    data = bytes(range(256)) * 40

    def handler(req: httpx.Request) -> httpx.Response:
        a, b = (int(x) for x in req.headers["range"].removeprefix("bytes=").split("-"))
        return httpx.Response(206, content=data[a : b + 1], headers={"content-range": f"bytes {a}-{b}/{len(data)}"})

    h = make(handler)
    first = h.get("https://api.example.org/f", headers={"Range": "bytes=0-99"})
    second = h.get("https://api.example.org/f", headers={"Range": "bytes=100-199"})
    assert first.status == 206 and first.body == data[:100] and second.body == data[100:200]


def test_fake_ip_proxy_range_only_with_owner_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    h = HostHttp(("api.example.org",), HttpPolicy(min_interval_s=0), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text="ok")), resolver=lambda host: ["198.18.0.1"], sleep=lambda s: None)
    with pytest.raises(HttpDenied, match="fake-IP"):
        h.get("https://api.example.org/x")
    monkeypatch.setattr(HostHttp, "allow_fake_ip", True)
    assert h.get("https://api.example.org/x").status == 200
    private = HostHttp(("api.example.org",), resolver=lambda host: ["10.0.0.5"])
    with pytest.raises(HttpDenied):  # still refused with the opt-in
        private.get("https://api.example.org/x")


def test_arxiv_politeness_floor() -> None:
    from researchos.services.knowledge.http_fetch import HOST_MIN_INTERVAL_S

    slept: list[float] = []
    t = {"now": 1000.0}
    h = HostHttp(("export.arxiv.org",), HttpPolicy(min_interval_s=0.5), transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text="ok")), resolver=PUBLIC, clock=lambda: t["now"], sleep=slept.append)
    HostHttp._last.pop("export.arxiv.org", None)
    h.get("https://export.arxiv.org/api/query?a=1")
    h.get("https://export.arxiv.org/api/query?a=2")
    assert slept and abs(slept[-1] - HOST_MIN_INTERVAL_S["export.arxiv.org"]) < 1e-9
