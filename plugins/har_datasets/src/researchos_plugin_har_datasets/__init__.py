"""Public contracts-only plugin manifest."""

from pathlib import Path

from researchos.contracts.execution import TemplatePackage
from researchos.contracts.plugins import PluginManifest

MANIFEST = PluginManifest(
    name="researchos_plugin_har_datasets",
    version="0.1.0",
    core_contract=">=1.7,<2",
    capabilities=["network:archive.ics.uci.edu", "network:www.cis.fordham.edu"],
    settings_schema={
        "type": "object",
        "properties": {
            "source_url": {"type": "string", "format": "uri"},
            "source_version": {"type": "string", "minLength": 1},
            "source_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
            "license": {"type": "string", "minLength": 1},
            "license_source": {"type": "string", "minLength": 1},
            "egress": {
                "enum": ["local_only", "cloud_redacted", "cloud_ok"],
                "default": "local_only",
                "description": "may content from this data reach cloud models (owner states, per license)",
            },
            "max_source_bytes": {"type": "integer", "minimum": 1, "maximum": 268435456},
            "max_expanded_bytes": {"type": "integer", "minimum": 1, "maximum": 268435456},
            "window_samples": {"type": "integer", "minimum": 2, "maximum": 4096},
            "max_gap_ns": {"type": "integer", "minimum": 1},
            "split_seed": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": False,
    },
)


def np_package() -> TemplatePackage:
    """Self-contained template directory (model=rf|cnn); nothing outside it is imported at run time."""
    return TemplatePackage(Path(__file__).parent / "templates" / "har_np")
