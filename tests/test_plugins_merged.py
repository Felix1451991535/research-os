"""Merged plugins (plugins/*) as the host actually loads them: real entry points, manifests, capability-limited
hosts, unreviewed plugin templates. Offline; skipped only if the `plugins` extra is not installed."""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.services import plugins as plugin_loader
from researchos.services.execution.templates import TemplateRegistry
from researchos.services.extensions import build_extensions

pytest.importorskip("researchos_plugin_litsearch")


def test_merged_plugins_load_with_capability_limited_hosts(tmp_path: Path) -> None:
    loaded = plugin_loader.load()
    assert not loaded.errors
    ext = build_extensions(loaded)
    assert not ext.rejected, ext.rejected
    assert {"lit_crossref", "lit_openalex", "lit_semantic", "lit_pubmed", "lit_arxiv"} <= set(ext.sources)
    assert {"har_uci", "har_wisdm"} <= set(ext.dataset_providers)
    assert "lit_selection" in ext.importers and {"lit_ris", "lit_bibtex"} <= set(ext.file_importers)
    assert {"report_docx", "report_latex", "markdown"} <= set(ext.exporters)
    key = next(k for k in ext.owners if k.endswith(":lit_crossref"))
    assert ext.granted_hosts(key, ("api.crossref.org", "evil.example")) == ("api.crossref.org",)
    reg = TemplateRegistry([], ext.template_roots, tmp_path / "REVIEWED.json")
    tpl = reg.get("har_np")
    assert tpl is not None and tpl.status == "unreviewed"  # owner must record its hash before any run
    assert tpl.manifest.requires == ("numpy", "sklearn")


def test_batch2_plugins_load_and_secret_fields_are_detected(tmp_path: Path) -> None:
    pytest.importorskip("researchos_plugin_zotero_import")
    from researchos.services import plugin_settings as ps

    loaded = plugin_loader.load()
    ext = build_extensions(loaded)
    assert not loaded.errors and not ext.rejected, (loaded.errors, ext.rejected)
    assert {"physio_mitbih", "physio_ptbxl", "physio_chbmit"} <= set(ext.dataset_providers)
    assert "zotero_web" in ext.importers and {"zotero_csljson", "zotero_rdf"} <= set(ext.file_importers)
    assert {
        "stats_diag_metrics",
        "stats_roc_auc",
        "stats_paired_tests",
        "stats_cluster_bootstrap",
        "stats_multiplicity",
        "stats_power",
    } <= set(ext.tools)
    assert all(b.spec.read_only and b.spec.side_effect_scope == "none" for b in ext.tools.values())
    assert "stats_report" in ext.exporters
    # secrets: entry-level schema (zotero_web) and manifest-level schema (litsearch semantic scholar key)
    assert ps.secret_fields(ps.schema(ext, "importers", "zotero_web")) == {"api_key"}
    assert ps.secret_fields(ps.schema(ext, "datasources", "lit_semantic")) == {"semantic_scholar_api_key"}
    reg = TemplateRegistry([], ext.template_roots, tmp_path / "REVIEWED.json")
    tpl = reg.get("physio_ecg_baseline")
    assert tpl is not None and tpl.status == "unreviewed"
