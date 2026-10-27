# researchos-plugin-zotero_import 0.1.0

Imports a Zotero library into Research OS (core contracts `>=1.3,<2`): file exports (CSL-JSON, Zotero RDF) and
selected items via the Zotero Web API v3. Imports only `researchos.contracts` / `researchos.sdk`. Network:
`api.zotero.org` only (capability `network:api.zotero.org`), through the host `HttpContext`. The local Zotero
desktop API (loopback) is not used; the host would refuse it anyway.

## Entry points (`researchos.importers`)
| Name | Kind | What |
|---|---|---|
| `zotero_csljson` | FileImporter (pure) | Zotero "CSL JSON" export -> BibRecords |
| `zotero_rdf` | FileImporter (pure) | Zotero RDF export -> BibRecords. DOCTYPE/ENTITY declarations are refused before parsing; defusedxml is used when installed (`[safe-xml]` extra) |
| `zotero_web` | Importer | writes selected records through the host KnowledgeWriter, re-fetching current metadata from the Web API |

## Mapping
- Identity: `("zotero", "<library>/<itemKey>")` (library = `users/<id>` or `groups/<id>`), a library-scoped hard
  identity in core 1.3 (legacy `("other", "zotero:...")` is still read). Previously in contracts 1.2
  have no `zotero` IdScheme, so this is proposed in PROPOSED_CONTRACT_CHANGES.md. The record `source_id` is
  `<library>/<itemKey>` when known.
- DOI lower-cased, without `https://doi.org/` or `doi:` prefix; PMID; PMCID (`PMC` prefix); arXiv id without the
  version suffix; all checksum-valid ISBN-10/13. These come from explicit fields first, then from Extra lines
  (`PMID: ...`, `PMCID: ...`, `arXiv: ...`, `DOI: ...`).
- Authors (creator type author only; names written entirely in CJK keep family-name-first order, e.g. 李明),
  title, container (publication, book, proceedings, website or repository title), year (only when the date
  states exactly one plausible 4-digit year), abstract, URL, item type -> `kind`, license only for a clearly
  stated open license in Rights (CC-BY-4.0, CC-BY-SA-4.0, CC0-1.0). Missing values are None, never "".
- Tags, collections (RDF: `Parent/Child` paths; API: collection keys), notes text and attachment metadata are
  parsed (`ZoteroItem`), but BibRecord has no fields for them yet, so they are **not written**. See
  PROPOSED_CONTRACT_CHANGES.md.
- Attachments are never downloaded or stored; attachment, note and annotation items are rejected with a reason.

## zotero_web behaviour
- A record with a Zotero reference is fetched with `GET /<library>/items?itemKey=<up to 50>&format=json`,
  following `Link: rel="next"` (validated to stay on api.zotero.org) or `Total-Results`.
- Records from `zotero_csljson`/`zotero_rdf` without a usable reference (RDF exports carry no item keys; local
  unsynced libraries; citation-key ids) are written exactly as parsed. A local-library item key is mapped to the
  configured library when `library_type`/`library_id` are set.
- Idempotency key `zotero:<library>/<key>@v<version>` (file records: source, id and content hash). An item
  repeated in one import is written once. Re-import is idempotent. A new item version gets a new key and the
  KnowledgeWriter dedupes it by DOI > PMID > arXiv. Without a hard identifier, a new version can create a second
  Work (see the risks in SELF_REVIEW.md).
- Errors are per library and per item, returned as `rejected` with a reason: HTTP 403 or 404, a host
  network denial, temporary unavailability ("retry later"), an item that was not returned, a missing title.

## Settings (`zotero_web`)
| Key | Type | Notes |
|---|---|---|
| `library_type` | `user`/`group` | needed together with `library_id`, to map bare or local item keys |
| `library_id` | digits string | Zotero user or group id |
| `api_key` | string, **secret** (`writeOnly`, `x-researchos-secret`) | sent only as the `Zotero-API-Key` header. It is never put in URLs, results, exceptions or logs, and messages are scrubbed. Leave it empty if the host injects the header itself |
| `refetch` | bool (true) | false: write file-parsed records as parsed, with no network |

## Limits
- Not run against the live Zotero API in this delivery (offline tests with synthetic fixtures only).
- FileImporters silently skip items without a title (the contract cannot return warnings). Zotero RDF
  `rdf:about` references (e.g. `#item_12`) are only stable within one export.
- The API key is a secret plugin setting: the owner sets it once in Research OS Settings
  (`PUT /api/v1/extensions/importers/zotero_web/settings`); it is stored backend-only and never sent by the frontend.

## Tests
`pytest`: ImporterConformance for `zotero_web`, plain pytest for both FileImporters, rule checks. All fixtures
are SYNTHETIC (`fixtures/README.md`).
