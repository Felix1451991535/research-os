"""Public contracts-only plugin manifest."""

from researchos.contracts.plugins import PluginManifest

MANIFEST = PluginManifest(
    name="researchos_plugin_report_export",
    version="0.1.0",
    core_contract=">=1.2,<2",
    capabilities=[],
    settings_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
