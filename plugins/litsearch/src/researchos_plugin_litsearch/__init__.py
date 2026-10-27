"""Public contracts-only plugin manifest."""

from researchos.contracts.plugins import PluginManifest

MANIFEST = PluginManifest(
    name="researchos_plugin_litsearch",
    version="0.3.0",
    core_contract=">=1.4,<2",
    capabilities=[
        "network:api.crossref.org",
        "network:api.openalex.org",
        "network:api.semanticscholar.org",
        "network:eutils.ncbi.nlm.nih.gov",
        "network:export.arxiv.org",
        "network:arxiv.org",
        "network:pmc.ncbi.nlm.nih.gov",
        "network:api.unpaywall.org",
    ],
    settings_schema={
        "type": "object",
        "properties": {
            "polite_email": {"type": "string"},
            "semantic_scholar_api_key": {"type": "string", "writeOnly": True},
        },
        "additionalProperties": False,
    },
)
