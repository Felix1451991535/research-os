# Proposed contract changes (contracts 1.2 -> 1.3); core was NOT patched

## 1. `IdScheme` has no `zotero`
The spec asks for `Identifier("zotero", "lib/key")`. `IdScheme` is a closed Literal without `zotero`, so the
plugin emits `("other", "zotero:<library>/<key>")`, with library `users/<id>` or `groups/<id>`.
Proposal: add `"zotero"` to `IdScheme`. Acceptance: InMemoryKnowledgeWriter/host keep it as a soft identifier
(no cross-library merge).

## 2. Zotero identity is not a hard ID for writer dedupe
The KnowledgeWriter dedupes only by DOI > PMID > arXiv. A Zotero item without any of these, re-imported after a
version bump (new idempotency key `...@v<new>`), creates a **second Work**. Proposal: let the writer treat
`zotero:<library>/<key>` (or a generic `(source, source_id)`) as a hard identity for updates. Acceptance: version
bump without DOI -> same work_id, new version_id.

## 3. BibRecord lacks Zotero fields
Tags, collections, notes, attachment metadata and the original item type are parsed (`ZoteroItem`) but cannot be
written: BibRecord has no fields for them. `kind` is mapped from the item type ("article" when unknown, the
contract default). Proposal: `BibRecord.extra: Mapping[str, JSON] = {}` or typed `tags`, `collections`,
`notes`, `attachments` fields. Acceptance: round-trip of the CSL-JSON fixture keeps tags/collections.

## 4. FileImporter cannot report warnings
Items without a title, notes and attachments are skipped silently by `parse_file` (it returns records only). The
Web importer can reject with a reason, the file importers cannot. Proposal:
`parse_file -> FileImportResult(records, diagnostics)`. Acceptance: skipped items are visible to the user.

## 5. Secret settings
There is no contract marker for secrets. `api_key` is declared `writeOnly: true`, `format: password` and
`x-researchos-secret: true` in `settings_schema`, and the plugin scrubs it from every message. Proposal: a
standard `x-researchos-secret` (or `writeOnly`) convention that the host encrypts at rest, never echoes back, and
redacts from logs. Better still, per-plugin header injection (e.g. `Zotero-API-Key`) by HostHttp, so plugins
never see the key.

## 6. HostHttp cache key ignores request headers (security)
HostHttp caches 200 responses keyed by URL only. Two requests to the same URL with different `Zotero-API-Key`
headers (two users or keys) could be served the same cached private response. Proposal: include auth-relevant
headers (or a hash of all request headers) in the cache key, or do not cache requests that carry credentials.
Acceptance: two keys, same URL -> two upstream calls.

## 7. Zotero RDF has no item keys
Zotero RDF exports carry no Zotero item keys (only `rdf:about` like `#item_12`, stable only within one export),
so RDF records carry no `zotero:` identifier. This deviates from "always emit the Zotero identifier"; CSL-JSON and
Web API records do carry it.
