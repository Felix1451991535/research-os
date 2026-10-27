"""Owner-managed plugin settings (core 1.3): secrets backend-only, never accepted in requests or returned."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

import pytest

from researchos.contracts.datasources import DataSourceSpec, SearchPage, SearchQuery
from researchos.contracts.errors import ApiError
from researchos.contracts.plugins import PluginManifest
from researchos.services import plugin_settings as ps
from researchos.services.container import Services
from researchos.services.extensions import Extensions

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "library_id": {"type": "string", "pattern": "^[0-9]+$"},
        "api_key": {"type": "string", "minLength": 8, "writeOnly": True},
    },
}


@dataclass
class KeyedSource:
    spec: DataSourceSpec = field(
        default_factory=lambda: DataSourceSpec(
            "keyed", "0.1.0", "bibliographic", "test", ("api.example.org",), settings_schema=SCHEMA
        )
    )
    seen: list[dict[str, Any]] = field(default_factory=list)

    def search(self, query: SearchQuery, http: Any, settings: Any) -> SearchPage:
        self.seen.append(dict(settings))
        return SearchPage(())


def ext_with(src: KeyedSource) -> Extensions:
    e = Extensions(sources={"keyed": src})
    e.owners["datasources:keyed"] = PluginManifest(name="test_plugin", version="0.1.0", core_contract=">=1.3,<2")
    return e


def test_secret_settings_round_trip_backend_only(conn: sqlite3.Connection, svc: Services) -> None:
    ext = ext_with(KeyedSource())
    v = ps.update(
        conn,
        svc.keystore,
        ext,
        "datasources",
        "keyed",
        {"library_id": "123", "api_key": "SYNTHETIC-KEY-0001"},
        "user:test",
    )
    assert v["values"] == {"library_id": "123"} and v["secrets"] == {"api_key": True}
    assert "SYNTHETIC-KEY-0001" not in repr(v)
    events = conn.execute("SELECT data FROM events WHERE kind='plugin.settings_changed'").fetchall()
    assert events and all("SYNTHETIC-KEY-0001" not in str(e[0]) for e in events)
    eff = ps.effective(conn, svc.keystore, ext, "datasources", "keyed", {"library_id": "456"})
    assert eff == {"library_id": "456", "api_key": "SYNTHETIC-KEY-0001"}
    with pytest.raises(ApiError) as e:
        ps.effective(conn, svc.keystore, ext, "datasources", "keyed", {"api_key": "SENT-BY-CLIENT"})
    assert e.value.code == "secret_in_request"
    ps.update(conn, svc.keystore, ext, "datasources", "keyed", {"api_key": None}, "user:test")
    assert ps.view(conn, svc.keystore, ext, "datasources", "keyed")["secrets"] == {"api_key": False}


def test_invalid_secret_error_never_echoes_value(conn: sqlite3.Connection, svc: Services) -> None:
    ext = ext_with(KeyedSource())
    ps.update(conn, svc.keystore, ext, "datasources", "keyed", {"api_key": "short"}, "user:test")
    with pytest.raises(ApiError) as e:
        ps.effective(conn, svc.keystore, ext, "datasources", "keyed", {})
    assert "short" not in str(e.value.message)


def test_settings_endpoints_and_search_use_stored_secret(client: Any, svc: Services) -> None:
    src = KeyedSource()
    svc.extensions.sources["keyed"] = src  # type: ignore[index]
    svc.extensions.owners["datasources:keyed"] = PluginManifest(
        name="test_plugin", version="0.1.0", core_contract=">=1.3,<2"
    )
    r = client.put("/api/v1/extensions/datasources/keyed/settings", json={"values": {"api_key": "SYNTHETIC-KEY-0002"}})
    assert r.status_code == 200 and r.json()["secrets"] == {"api_key": True} and "SYNTHETIC" not in r.text
    assert "SYNTHETIC" not in client.get("/api/v1/extensions/datasources/keyed/settings").text
    assert client.post("/api/v1/sources/keyed/search", json={"text": "x"}).status_code == 200
    assert src.seen[-1]["api_key"] == "SYNTHETIC-KEY-0002"
    bad = client.post("/api/v1/sources/keyed/search", json={"text": "x", "settings": {"api_key": "X" * 10}})
    assert bad.status_code == 422 and bad.json()["code"] == "secret_in_request"
