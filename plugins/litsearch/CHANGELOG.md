# Changelog

## 0.3.1 (2026-10-13)
- Fix (found by a live check): PubMed EFetch XML with its standard external DOCTYPE was rejected; plain external
  DOCTYPE references are now removed before parsing, internal subsets / ENTITY declarations still refused.

## 0.3.0 (2026-10-13)
- Portable filters `author` (author watch) and `from_year` (catch-up), mapped per source; others refused.

## 0.2.0 (2026-10-13, core 1.4)
- `lit_openalex` gains citation chaining (`references` via `cited_by:`, `cited_by` via `cites:`) and retraction
  status (`is_retracted`; absent = unknown).
- New OA resolver `lit_unpaywall` (needs `polite_email`; licence only from Unpaywall's explicit field).
- `core_contract >=1.4,<2`; manifest capability `network:api.unpaywall.org`.

## 0.1.0+ros1 (merged into Research OS, 2026-10-12)
- Host review fix: one malformed upstream record (e.g. a Crossref item without a title) is skipped and reported in
  `SearchPage.warnings` instead of failing the whole page (all five sources). A malformed envelope still raises.

## 0.1.0
Replaced the v1 manifest-only scaffold with contracts 1.2 implementations and offline tests.
