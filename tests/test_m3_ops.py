"""M3 ops: private-network HTTPS (local CA, iOS lifetime, SAN checks), doctor, LAN bind refusal, device reuse,
CA download, isolated parsing with a hard timeout."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI

from researchos.kernel.config import Settings
from researchos.services.container import Services
from researchos.services.ops.doctor import run_checks
from researchos.services.ops.tlscert import inspect_cert, make_server_cert

from .conftest import PASSWORD, Client
from .gold.g1_generator import single_column
from .m1_helpers import drain


def _status(items: list[dict[str, str]], check: str) -> str:
    return next(i["status"] for i in items if i["check"] == check)


def test_local_ca_cert_valid_for_ios(tmp_path: Path) -> None:
    res = make_server_cert(tmp_path / "tls", ["192.168.1.20", "my-pc.local"])
    info = inspect_cert(res["cert"])
    assert set(info["hosts"]) == {"192.168.1.20", "my-pc.local"}
    assert info["validity_days"] <= 825 and not info["expired"]
    again = make_server_cert(tmp_path / "tls", ["192.168.1.21"])  # reuses the same CA
    assert Path(again["ca_cert"]).read_bytes() == Path(res["ca_cert"]).read_bytes()


def test_doctor_tls_san_mismatch_and_https_origin(settings: Settings, tmp_path: Path) -> None:
    res = make_server_cert(tmp_path / "tls", ["192.168.1.20"])
    settings.host, settings.tls_cert_file, settings.tls_key_file = "192.168.1.20", res["cert"], res["key"]
    settings.public_origins = ["https://192.168.1.20:8443", "https://other.local:8443"]
    items = run_checks(settings)
    assert _status(items, "network_config") == "PASS"
    assert _status(items, "tls_san:192.168.1.20") == "PASS" and _status(items, "tls_san:other.local") == "FAIL"
    assert _status(items, "gpu_preflight") in ("NOT RUN", "PASS", "FAIL")


def test_lan_bind_without_tls_refused(settings: Settings) -> None:
    settings.host, settings.public_origins = "192.168.1.20", ["http://192.168.1.20:8443"]
    assert any("without TLS" in p for p in settings.validate_network())
    items = run_checks(settings)
    assert _status(items, "network_config") == "FAIL"
    assert any(i["check"].startswith("origin_https") and i["status"] == "WARN" for i in items)


def test_doctor_reports_gpu_preflight_not_run(app: FastAPI, settings: Settings) -> None:
    assert _status(run_checks(settings), "gpu_preflight") == "NOT RUN"


def test_relogin_reuses_device(client: Client, app: FastAPI) -> None:
    c = Client(app)
    for _ in range(3):
        assert c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    assert len(client.get("/api/v1/devices").json()) == 2  # setup device + one re-used browser device


def test_ca_cert_download(client: Client, settings: Settings) -> None:
    assert client.get("/ca.crt").status_code == 404
    make_server_cert(settings.config_dir / "tls", ["127.0.0.1"])
    r = client.get("/ca.crt")
    assert r.status_code == 200 and b"BEGIN CERTIFICATE" in r.content


def test_parse_hard_timeout(client: Client, conn: sqlite3.Connection, svc: Services, settings: Settings) -> None:
    settings.parse_timeout_s = 0  # type: ignore[assignment]
    r = client.post("/api/v1/documents/upload", files={"file": ("a.pdf", single_column().data, "application/pdf")})
    drain(conn, svc)
    doc = client.get(f"/api/v1/documents/{r.json()['version_id']}").json()
    assert doc["parse_state"] == "failed" and doc["parse_error"].startswith("timeout") and doc["recovery_action"]
