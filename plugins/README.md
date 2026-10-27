# Merged plugins

Third-party plugins built offline against core contracts 1.2 (PluginKit v2), reviewed and merged by the core
maintainer. Each is a uv workspace member installed by the `plugins` extra (`uv sync --extra plugins`; the
installers and selftest do this). Plugins import only `researchos.contracts` / `researchos.sdk`; `scripts/check.sh`
runs each plugin's own ruff, `mypy --strict` and offline tests.

Installing a plugin registers its entry points but grants nothing by itself: network access goes only through
HostHttp limited to `spec.hosts ∩ manifest network: capabilities`; imports need a `knowledge_import` approval;
plugin templates stay **unreviewed** until the owner records their hash in `<data>/config/REVIEWED.json`
(`scripts/review_template.py`).

| plugin | provides | source bundle (sha256 of the received zip) |
|---|---|---|
| litsearch | datasources lit_crossref, lit_openalex, lit_semantic (needs key), lit_pubmed, lit_arxiv; importer lit_selection; file importers lit_ris, lit_bibtex | researchos-plugin-litsearch-0.1.0.zip `8c77c17d…abafc` |
| har_datasets | dataset providers har_uci, har_wisdm; template har_np (model=rf\|cnn, CPU) | researchos-plugin-har_datasets-0.1.0.zip `f0b6315e…caf41` |
| report_export | exporters report_docx, report_latex (LaTeX source; no PDF compilation) | researchos-plugin-report_export-0.1.0.zip `3109c809…157f` |
| physio_signals | dataset providers physio_mitbih, physio_ptbxl, physio_chbmit (PhysioNet, patient-level splits); template physio_ecg_baseline | researchos-plugin-physio_signals-0.1.0.zip `a73a4600…3b6f` |
| zotero_import | file importers zotero_csljson, zotero_rdf; importer zotero_web (API key = owner secret setting) | researchos-plugin-zotero_import-0.1.0.zip `3ac82e26…c057` |
| diag_stats | read-only tools stats_diag_metrics, stats_roc_auc, stats_paired_tests, stats_cluster_bootstrap, stats_multiplicity, stats_power, stats_compare_groups, stats_survival; exporter stats_report | researchos-plugin-diag_stats-0.1.0.zip `4baa9a0c…adff` |
| medimg_dicom | dataset provider medimg_dicom_folder (local DICOM via core 1.5 `files:read`, PHI preflight, pseudonymous groups; research use only) | built in this repo (BIOMED P5, ADR-156) |

Changes made during the merge review are listed in each plugin's CHANGELOG (`0.1.0+ros1`); the decision record
is ADR-076…080 (batch 1) and ADR-083…087 (batch 2) in docs/DECISIONS.md. Secret settings (Zotero API key,
Semantic Scholar key) are set by the owner in Settings and stored backend-only (ADR-085). Upstream availability of the real APIs and the real WISDM/UCI archives was
not exercised (offline review; no network calls made).
