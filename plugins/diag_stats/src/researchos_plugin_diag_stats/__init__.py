"""Research OS plugin diag_stats: read-only diagnostic-accuracy statistics tools and the stats_report exporter.

Imports only researchos.contracts / researchos.sdk from core. No network, no settings, no file writes from tools;
the exporter writes only inside ExportContext.out_dir.
"""

from __future__ import annotations

from researchos.contracts.plugins import PluginManifest

__version__ = "0.1.0"

MANIFEST = PluginManifest(
    name="researchos_plugin_diag_stats",
    version=__version__,
    core_contract=">=1.8,<2",
    capabilities=[],
    settings_schema={},
)
