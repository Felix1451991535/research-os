"""PhysioNet ECG/EEG dataset providers + ECG baseline template. Imports ONLY researchos.contracts / researchos.sdk."""

from __future__ import annotations

from pathlib import Path

from researchos.contracts.execution import TemplatePackage
from researchos.contracts.plugins import PluginManifest

from . import chbmit, eegmmi, mitbih, ptbxl, synth_eeg

MANIFEST = PluginManifest(
    name="researchos_plugin_physio_signals",
    version="0.1.0",
    core_contract=">=1.7,<2",
    capabilities=["network:physionet.org", "files:read"],  # files:read: local copies (ADR-189)
    settings_schema={
        "type": "object",
        "description": "Per-provider build settings (passed as dataset.build settings)",
        "properties": {
            "physio_mitbih": mitbih.SETTINGS_SCHEMA,
            "physio_ptbxl": ptbxl.SETTINGS_SCHEMA,
            "physio_chbmit": chbmit.SETTINGS_SCHEMA,
            "physio_eegmmi": eegmmi.SETTINGS_SCHEMA,
            "physio_synth_eeg": synth_eeg.SETTINGS_SCHEMA,
        },
        "additionalProperties": False,
    },
    migration_namespace=None,
)


def template_package() -> TemplatePackage:
    return TemplatePackage(Path(__file__).parent / "templates" / "physio_ecg_baseline")
