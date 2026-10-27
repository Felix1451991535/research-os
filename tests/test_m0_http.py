"""M0 HTTP surface: error body, correlation ids, host/origin/CSRF checks, IPv6 host parsing, secrets hygiene."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from researchos.api.middleware import parse_host
from researchos.kernel.logsetup import RedactFilter, redact

from .conftest import Client


def test_health_and_error_body(anon: Client) -> None:
    assert anon.get("/api/v1/health").json()["db_ok"] is True
    r = anon.get("/api/v1/projects")
    b = r.json()
    assert r.status_code == 401 and b["code"] == "unauthenticated"
    assert set(b) >= {"code", "message", "retryable", "correlation_id"}
    assert r.headers["x-correlation-id"] == b["correlation_id"]
    assert anon.get("/api/v1/nope").json()["code"] == "not_found"


def test_origin_host_csrf(client: Client) -> None:
    r = client.post("/api/v1/projects", json={"title": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "bad_origin"
    raw = TestClient(client.app, cookies=client.cookies)
    r = raw.post("/api/v1/projects", json={"title": "x"}, headers={"idempotency-key": "k"})
    assert r.status_code == 403 and r.json()["code"] == "missing_client_header"
    assert client.get("/api/v1/health", headers={"host": "attacker.example"}).status_code == 421
    assert client.get("/api/v1/health", headers={"host": "[::1]:8443"}).status_code == 200
    assert client.post("/api/v1/projects", json={"title": "ok"}).status_code == 201


@pytest.mark.parametrize("raw,host", [("a.b:8443", "a.b"), ("A.B", "a.b"), ("[::1]:8443", "::1"),
                                      ("[fe80::1]", "fe80::1"), ("fe80::1", "fe80::1"), ("127.0.0.1", "127.0.0.1")])
def test_parse_host(raw: str, host: str) -> None:
    assert parse_host(raw) == host


def test_keys_write_only_and_redaction(client: Client) -> None:
    secret = "sk-test-SECRETVALUE1234567890"
    r = client.put("/api/v1/settings/keys/deepseek", json={"api_key": secret})
    assert r.status_code == 200 and r.json() == {"configured": True, "source": "file"}
    body = client.get("/api/v1/settings/keys").text + client.get("/api/v1/events").text
    assert "SECRETVALUE" not in body
    assert redact(f"key {secret}") == "key [REDACTED]"
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "Authorization: Bearer %s", (secret,), None)
    RedactFilter().filter(rec)
    assert secret not in rec.getMessage()


def test_security_headers(anon: Client) -> None:
    h = anon.get("/api/v1/health").headers
    assert h["x-frame-options"] == "DENY" and "default-src 'self'" in h["content-security-policy"]
    assert h["cache-control"] == "no-store"
