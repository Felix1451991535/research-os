"""Owner-managed plugin settings (core 1.3, ADR-085). Secret fields never travel through the frontend.

A field is secret if its JSON-Schema property has `writeOnly: true`, `format: "password"` or
`x-researchos-secret: true`. Non-secret values live in settings_kv; secret values live in the backend SecretStore
(`<data>/config/secrets.env`, or an env var of the same name) and are registered for log redaction. Requests may
override non-secret fields per call; a request that carries a secret field is refused. Plugins receive the merged
settings, validated against their schema.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from typing import Any

import jsonschema

from ..contracts.errors import ApiError
from ..kernel.db import jdump, jload, row, tx, utcnow
from ..kernel.events import emit
from .extensions import Extensions
from .gateway.keystore import SecretStore

GROUP_TABLES = {
    "datasources": ("sources", "dataset_providers", "oa_resolvers"),
    "importers": ("importers", "file_importers"),
    "exporters": ("exporters",),
}


def _entry(ext: Extensions, group: str, name: str) -> Any:
    for table in GROUP_TABLES.get(group, ()):
        obj = getattr(ext, table).get(name)
        if obj is not None:
            return obj
    raise ApiError(404, "unknown_extension", f"no {group} entry {name!r}")


def schema(ext: Extensions, group: str, name: str) -> dict[str, Any]:
    """The entry's own spec.settings_schema; else its manifest's schema (a per-entry sub-schema if keyed by name)."""
    own = dict(getattr(getattr(_entry(ext, group, name), "spec", None), "settings_schema", {}) or {})
    if own:
        return own
    m = ext.owners.get(f"{group}:{name}")
    s: dict[str, Any] = dict(m.settings_schema) if m else {}
    props = s.get("properties")
    sub = props.get(name) if isinstance(props, dict) else None
    return dict(sub) if isinstance(sub, dict) and sub.get("type") == "object" else s


def secret_fields(sch: Mapping[str, Any]) -> set[str]:
    props = sch.get("properties") or {}
    return {k for k, v in props.items() if isinstance(v, dict) and (
        v.get("writeOnly") or v.get("format") == "password" or v.get("x-researchos-secret"))}


def _env_name(group: str, name: str, field: str) -> str:
    return "ROS_PLUGIN_" + re.sub(r"[^A-Z0-9]+", "_", f"{group}_{name}_{field}".upper())


def _kv_key(group: str, name: str) -> str:
    return f"plugin_settings:{group}:{name}"


def view(conn: sqlite3.Connection, ks: SecretStore, ext: Extensions, group: str, name: str) -> dict[str, Any]:
    sch = schema(ext, group, name)
    r = row(conn.execute("SELECT value_json FROM settings_kv WHERE key=?", (_kv_key(group, name),)))
    return {"schema": sch, "values": jload(r["value_json"]) if r else {},
            "secrets": {f: ks.has_named(_env_name(group, name, f)) for f in sorted(secret_fields(sch))}}


def update(conn: sqlite3.Connection, ks: SecretStore, ext: Extensions, group: str, name: str,
           values: Mapping[str, Any], actor: str) -> dict[str, Any]:
    """Owner action. Secret fields: a string sets, null clears. Non-secret fields replace the stored values."""
    sch = schema(ext, group, name)
    secrets = secret_fields(sch)
    plain = {k: v for k, v in values.items() if k not in secrets}
    unknown = set(values) - set(sch.get("properties") or {}) if sch.get("additionalProperties") is False else set()
    if unknown:
        raise ApiError(422, "invalid_settings", f"unknown settings {sorted(unknown)}")
    for f in secrets & set(values):
        v = values[f]
        if v is not None and not isinstance(v, str):
            raise ApiError(422, "invalid_settings", f"{f} must be a string or null")
        ks.set_named(_env_name(group, name, f), v)
    with tx(conn):
        conn.execute("INSERT INTO settings_kv(key,value_json,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
                     "SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                     (_kv_key(group, name), jdump(plain), utcnow()))
        emit(conn, "plugin.settings_changed", actor=actor, entity_type="extension", entity_id=f"{group}:{name}",
             data={"fields": sorted(plain), "secrets_changed": sorted(secrets & set(values))})
    return view(conn, ks, ext, group, name)


def effective(conn: sqlite3.Connection, ks: SecretStore, ext: Extensions, group: str, name: str,
              request: Mapping[str, Any] | None) -> dict[str, Any]:
    """Stored settings + per-call non-secret overrides + backend secrets, validated against the schema."""
    sch = schema(ext, group, name)
    secrets = secret_fields(sch)
    sent = set(request or {}) & secrets
    if sent:
        raise ApiError(422, "secret_in_request", f"secret settings {sorted(sent)} are set by the owner in Settings "
                                                  "(backend only), never sent with a request")
    r = row(conn.execute("SELECT value_json FROM settings_kv WHERE key=?", (_kv_key(group, name),)))
    merged: dict[str, Any] = {**(jload(r["value_json"]) if r else {}), **dict(request or {})}
    for f in secrets:
        v = ks.get_named(_env_name(group, name, f))
        if v:
            merged[f] = v
    if sch:
        try:
            jsonschema.validate(merged, sch)
        except jsonschema.ValidationError as e:
            raise ApiError(422, "invalid_settings", e.message if not secrets & set(e.path) else "invalid secret") \
                from None
    return merged
