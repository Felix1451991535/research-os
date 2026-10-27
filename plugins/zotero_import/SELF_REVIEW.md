# Self review: zotero_import 0.1.0

Status: all gates green offline (ruff, mypy --strict, pytest; no skip/xfail). This is a self review by the
building agent; **no independent review** was done. It was not run against the live Zotero API.

## Scope vs. BATCH2_SPEC
| Requirement | Where | Evidence |
|---|---|---|
| FileImporter `zotero_csljson` + `zotero_rdf` (safe XML) | `csljson.py`, `rdf.py` | plain pytest in `test_file_importers.py` incl. `test_rdf_unsafe_or_malformed_xml_is_refused` (DOCTYPE/ENTITY/XXE refused) |
| Importer `zotero_web`, api.zotero.org via HttpContext only | `web.py` | ImporterConformance; `test_undeclared_host_or_denied_network_rejects_with_reason`; no loopback (`test_no_network_libraries_or_loopback_in_source`) |
| API key = secret setting, never logged | `web.SETTINGS_SCHEMA`, `scrub` | `test_api_key_only_in_header_never_in_urls_results_errors_or_logs`, `test_no_api_key_means_no_key_header` |
| `zotero` lib/key identifier + DOI/PMID/PMCID/arXiv/ISBN | `model.py`, `ids.py` | `test_doi_normalisation`, `test_other_identifier_normalisation` |
| Map authors/title/container/year (valid only)/item type/tags/collections/notes; attachments metadata only | `webjson.py`, `csljson.py`, `rdf.py` | roundtrip tests, `test_year_and_license_rules`, `test_csljson_parsed_extras_kept_on_item` |
| Idempotency keys = zotero key + version; re-import idempotent; duplicates not double-written | `web.py` | `test_idempotency_keys_use_zotero_key_and_version`, `test_reimport_is_idempotent`, `test_duplicate_items_in_one_import_are_written_once`, `test_new_version_reuses_the_work_via_doi` |
| Pagination Link / Total-Results | `web.paginate` | `test_results_mapping_and_pagination`, `test_total_results_pagination_without_link`, `test_pagination_link_to_another_host_is_refused` |
| Imports only researchos.contracts/sdk | all | `test_only_public_core_modules_are_imported` |

## Conformance suites (researchos.sdk), all subclassed in tests/
| Test class | researchos.sdk suite | Result |
|---|---|---|
| `TestZoteroWeb` | `ImporterConformance` | 3 passed |
FileImporters have no sdk suite and are covered by plain pytest (`tests/test_file_importers.py`), as the spec
says.

## Environment
Windows (win32 10.0.26300); Python 3.11.15 in the core_snapshot `.venv`. ruff 0.17.0, mypy 2.4.0, pytest 9.1.1,
jsonschema 4.26, pydantic 2.14.1. defusedxml is **not** installed in this environment, so the tests exercise the
stdlib parser behind the DOCTYPE/ENTITY pre-screen. The optional `[safe-xml]` extra adds defusedxml
(`forbid_dtd=True`). Gates run against the installed wheel. The core is unmodified.

## Known gaps (see PROPOSED_CONTRACT_CHANGES.md)
- `IdScheme` has no `zotero`: emitted as `("other", "zotero:<library>/<key>")`.
- Tags, collections, notes and attachment metadata are parsed but not written (BibRecord has no fields).
- FileImporters cannot return warnings: untitled items, notes and attachments are skipped silently.
- Zotero RDF exports have no item keys, so RDF records carry no zotero identifier.

## Risks
- A new item version without DOI/PMID/arXiv creates a second Work (the writer has no hard Zotero identity).
- HostHttp caches 200 responses by URL only (request headers ignored). With several API keys for the same
  library URL, a cached private response could be reused across keys. This is a host issue, reported as a
  contract change.
- Mapping was checked against synthetic fixtures modelled on the Zotero export formats and the Web API v3 docs,
  not against real exports from every Zotero version. Item types outside the mapped table fall back to
  "article" (contract default).
- The API key lives in plugin settings; at-rest protection depends on the host (no secret contract yet).

## Recorded gate output
```text
$ python -m build --wheel --no-isolation
Successfully built researchos_plugin_zotero_import-0.1.0-py3-none-any.whl
WARNING Both NO_COLOR and FORCE_COLOR environment variables are set, disabling color
* Getting build dependencies for wheel...
* Building wheel...
(exit 0)
```

```text
$ pip install --force-reinstall --no-deps dist/researchos_plugin_zotero_import-0.1.0-py3-none-any.whl
Successfully installed researchos-plugin-zotero-import-0.1.0
(exit 0)
```

```text
$ ruff check src tests
All checks passed!
(exit 0)
```

```text
$ mypy --strict src tests
Success: no issues found in 11 source files
(exit 0)
```

```text
$ pytest -v (per file)
tests/test_boundaries.py PASSED: 5
tests/test_file_importers.py PASSED: 29
tests/test_web_importer.py PASSED: 17
============================= 51 passed in 0.57s ==============================
(exit 0)
```
