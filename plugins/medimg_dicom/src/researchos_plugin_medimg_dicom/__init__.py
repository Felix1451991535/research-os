"""Local DICOM folder dataset provider with a PHI preflight (research use only). Imports ONLY researchos.contracts /
researchos.sdk. Reads files solely through the host's read-only `ctx.local` (capability files:read, owner roots)."""

from __future__ import annotations

from researchos.contracts.plugins import PluginManifest

from .provider import SETTINGS_SCHEMA, VERSION

MANIFEST = PluginManifest(
    name="researchos_plugin_medimg_dicom",
    version=VERSION,
    core_contract=">=1.7,<2",
    capabilities=["files:read"],
    settings_schema={
        "type": "object",
        "description": "Per-provider build settings (passed as dataset.build settings)",
        "properties": {"medimg_dicom_folder": SETTINGS_SCHEMA},
        "additionalProperties": False,
    },
    migration_namespace=None,
)
