"""Zotero library import (CSL-JSON / RDF exports + Web API). Imports ONLY researchos.contracts / researchos.sdk."""

from __future__ import annotations

from researchos.contracts.plugins import PluginManifest

from .web import SETTINGS_SCHEMA

MANIFEST = PluginManifest(
    name="researchos_plugin_zotero_import",
    version="0.1.0",
    core_contract=">=1.3,<2",
    capabilities=["network:api.zotero.org"],
    settings_schema={
        "type": "object",
        "description": "Settings of the zotero_web importer (the file importers take none)",
        "properties": {"zotero_web": SETTINGS_SCHEMA},
        "additionalProperties": False,
    },
    migration_namespace=None,
)
