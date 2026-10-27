# Changelog

## 0.1.0+ros1 (merged into Research OS, 2026-10-12)
- core 1.3: identity emitted as `("zotero", "<library>/<key>")` (hard, library-scoped; legacy form still read);
  `core_contract >=1.3,<2`. The API key is an owner-managed secret setting (backend-only).

## 0.1.0 (2026-10-12)
- FileImporters `zotero_csljson` (Zotero CSL-JSON export) and `zotero_rdf` (Zotero RDF export; DOCTYPE/ENTITY
  refused before parsing, defusedxml used when installed).
- Importer `zotero_web`: re-fetches selected items from the Zotero Web API v3 (`api.zotero.org` only, via the
  host HttpContext). Pagination through `Link: rel="next"` / `Total-Results`. The API key is a write-only secret
  setting, sent only as a header and scrubbed from every message.
- Identifiers: `("other", "zotero:<library>/<key>")` + normalised DOI, PMID, PMCID, arXiv, ISBN (checksum).
- Idempotency key `zotero:<library>/<key>@v<version>`; duplicates within one import are written once;
  re-import is a no-op. Dedupe across items is left to the KnowledgeWriter (DOI > PMID > arXiv).
- Attachments: metadata only, never downloaded. No local (loopback) Zotero API.
