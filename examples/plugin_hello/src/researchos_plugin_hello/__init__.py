"""Reference plugin. Imports ONLY researchos.contracts and researchos.sdk (the public, versioned surface)."""

from __future__ import annotations

from pathlib import Path

from researchos.contracts.execution import TemplatePackage
from researchos.contracts.plugins import PluginManifest

MANIFEST = PluginManifest(
    name="researchos_plugin_hello",
    version="0.1.0",
    core_contract=">=1.7,<2",
    capabilities=["network:api.crossref.org"],  # hosts this plugin may contact (host enforces)
)


def template_package() -> TemplatePackage:
    return TemplatePackage(Path(__file__).parent / "templates" / "hello_centroid")
