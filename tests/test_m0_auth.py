"""M0 identity: setup token, host-only setup, login limiter, cookies, pairing, revocation, re-auth."""
from __future__ import annotations

import time

from fastapi import FastAPI

from researchos.kernel.config import Settings
from researchos.services.identity.credentials import verify_password
from researchos.services.identity.ratelimit import client_key

from .conftest import PASSWORD, Client


def test_setup_requires_token_once(anon: Client, settings: Settings) -> None:
    st = anon.get("/api/v1/auth/state").json()
    assert st["initialized"] is False and st["setup_allowed"] is True
    r = anon.post("/api/v1/auth/setup", json={"username": "a", "password": PASSWORD, "setup_token": "wrongtoken"})
    assert r.status_code == 403 and r.json()["code"] == "bad_setup_token"
    tok = (settings.config_dir / "setup-token.txt").read_text().strip()
    assert anon.post("/api/v1/auth/setup", json={"username": "a", "password": "short", "setup_token": tok}
                     ).status_code == 422
    r = anon.post("/api/v1/auth/setup", json={"username": "a", "password": PASSWORD, "setup_token": tok})
    assert r.status_code == 200 and r.json()["is_host"] is True
    assert not (settings.config_dir / "setup-token.txt").exists()
    assert anon.post("/api/v1/auth/setup", json={"username": "b", "password": PASSWORD, "setup_token": tok}
                     ).status_code == 403


def test_setup_rejected_from_lan_and_via_proxy(app: FastAPI, settings: Settings) -> None:
    tok = (settings.config_dir / "setup-token.txt").read_text().strip()
    body = {"username": "a", "password": PASSWORD, "setup_token": tok}
    r = Client(app, client=("192.168.1.50", 5000)).post("/api/v1/auth/setup", json=body)
    assert r.status_code == 403 and r.json()["code"] == "setup_local_only"
    r = Client(app).post("/api/v1/auth/setup", json=body, headers={"x-forwarded-for": "192.168.1.50"})
    assert r.status_code == 403


def test_login_limiter_and_cookie(client: Client, app: FastAPI) -> None:
    lan = Client(app, client=("10.0.0.9", 1))
    for _ in range(5):
        assert lan.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}
                        ).status_code == 401
    assert lan.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 429
    r = Client(app).post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 200
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc


def test_forwarded_for_only_when_trusted() -> None:
    assert client_key("127.0.0.1", "10.1.1.1", False) == "127.0.0.1"
    assert client_key("127.0.0.1", "10.1.1.1, 1.2.3.4", True) == "10.1.1.1"
    assert client_key("10.0.0.2", "10.1.1.1", True) == "10.0.0.2"


def test_unknown_user_costs_a_real_bcrypt_check() -> None:
    t = time.perf_counter()
    assert verify_password("anything", None) is False
    assert time.perf_counter() - t > 0.02  # dummy hash is a valid bcrypt hash, not an instant failure


def test_pairing_revocation_and_concurrent_devices(client: Client, app: FastAPI) -> None:
    code = client.post("/api/v1/auth/pairing-codes", json={"device_name": "iPad"}).json()["code"]
    ipad = Client(app, client=("192.168.1.30", 4000))
    assert ipad.post("/api/v1/auth/pair", json={"code": "WRONGCOD"}).status_code == 401
    r = ipad.post("/api/v1/auth/pair", json={"code": code, "device_name": "iPad"})
    assert r.status_code == 200 and r.json()["is_host"] is False
    dev = r.json()["device_id"]
    assert Client(app).post("/api/v1/auth/pair", json={"code": code}).status_code == 401  # single use
    assert ipad.get("/api/v1/projects").status_code == 200 and client.get("/api/v1/projects").status_code == 200
    assert {d["name"] for d in client.get("/api/v1/devices").json()} == {"desktop", "iPad"}
    assert client.post(f"/api/v1/devices/{dev}/revoke").status_code == 200
    assert ipad.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/projects").status_code == 200


def test_reauth(client: Client) -> None:
    assert client.post("/api/v1/auth/reauth", json={"password": "nope-nope-nope"}).status_code == 401
    assert client.post("/api/v1/auth/reauth", json={"password": PASSWORD}).status_code == 200
