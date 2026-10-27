from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from researchos.app.factory import create_app
from researchos.kernel.config import Settings, load_settings
from researchos.kernel.db import connect
from researchos.services.container import Services
from researchos.services.wiring import build_services

PASSWORD = "correct-horse-battery"


def write_config(data_dir: Path, **kv: Any) -> None:
    (data_dir / "config").mkdir(parents=True, exist_ok=True)
    lines = []
    for k, v in kv.items():
        lines.append(f"{k} = {str(v).lower() if isinstance(v, bool) else repr(v) if isinstance(v, str) else v}")
    (data_dir / "config" / "researchos.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_real_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never see real provider/relay keys or relay settings from the developer's environment."""
    from researchos.services.gateway.keystore import PROVIDER_ENV

    for k in (*PROVIDER_ENV.values(), "RESEARCHOS_RELAY_BASE_URL", "RESEARCHOS_RELAY_FOR", "RESEARCHOS_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    data = tmp_path / "data"
    write_config(data, heartbeat_s=0.2, lease_s=1.0)
    s = load_settings(data)
    s.web_dist = tmp_path / "no-web"
    s.extra_allowed_hosts = ["testserver"]
    s.extra_template_dirs = [str(Path(__file__).parent / "fixtures" / "templates")]
    return s


@pytest.fixture
def mocks() -> dict[str, Any]:
    from .m1_helpers import mock_providers
    return dict(mock_providers())


@pytest.fixture
def svc(settings: Settings, mocks: dict[str, Any]) -> Services:
    # Mock providers are registered under the real provider names: tests never reach the network.
    return build_services(settings, load_plugins=False, providers=mocks)


class Client(TestClient):
    """Sends the CSRF client header and a fresh Idempotency-Key on writes unless idem=False.
    Defaults the peer address to 127.0.0.1 (the host itself); pass client=(ip, port) to simulate LAN devices."""

    def __init__(self, app: Any, client: tuple[str, int] = ("127.0.0.1", 50000), **kw: Any) -> None:
        super().__init__(app, client=client, **kw)

    def request(self, method: str, url: Any, *a: Any, headers: dict[str, str] | None = None,  # type: ignore[override]
                idem: bool = True, **kw: Any) -> Any:
        h = {"x-researchos-client": "test"}
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE") and idem:
            h["idempotency-key"] = uuid.uuid4().hex
        h.update(headers or {})
        return super().request(method, url, *a, headers=h, **kw)


@pytest.fixture
def app(settings: Settings, svc: Services) -> FastAPI:
    return create_app(settings, svc)


@pytest.fixture
def anon(app: FastAPI) -> Client:
    return Client(app)


@pytest.fixture
def client(app: FastAPI, settings: Settings) -> Client:
    c = Client(app)
    tok = (settings.config_dir / "setup-token.txt").read_text().strip()
    r = c.post("/api/v1/auth/setup", json={"username": "admin", "password": PASSWORD, "device_name": "desktop",
                                           "setup_token": tok})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def conn(settings: Settings, app: FastAPI) -> Iterator[Any]:
    c = connect(settings.db_path)
    yield c
    c.close()
