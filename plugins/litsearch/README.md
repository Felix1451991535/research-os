# Research OS litsearch 0.1.0

Read-only literature search, pure bibliography-file parsing, and selected-record import.

## Registered objects

| Group | Entries |
| --- | --- |
| datasources | lit_crossref, lit_openalex, lit_pubmed, lit_arxiv, lit_semantic |
| importers | lit_selection, lit_ris, lit_bibtex |

The five search sources implement BibliographicSource; `lit_selection` implements
Importer, and the two file parsers implement FileImporter. Semantic Scholar is
inactive without its settings key. Crossref/OpenAlex use opaque cursors; PubMed
uses ESearch then EFetch; arXiv uses Atom and offsets. Searches are bounded to
1–100 records. Unknown filters raise an error rather than being ignored.
Malformed provider records raise an error; non-200 responses return page warnings.
Absent optional metadata stays None; blank titles or source identities are errors.
DOIs are normalized, and arXiv version suffixes are removed from identifiers while
source URLs preserve the provider version.

## Settings and host controls

`polite_email`: optional contact email for Crossref/PubMed.
`semantic_scholar_api_key`: optional write-only secret setting, passed only in
`x-api-key`. Never place it in query text or source files. Other provider credentials
must be injected by the host's HTTP policy if required. A source's availability
and external authentication requirements are not asserted by offline tests.

Network capabilities list only the five API hosts plus `arxiv.org` and
`pmc.ncbi.nlm.nih.gov`. HTTP timeout, retry, rate limiting, caching, size caps and
redirect/SSRF enforcement belong to the supplied HttpContext, not this plugin.
No direct HTTP client or socket code exists here.

## Import behavior

Send only owner-selected records to `lit_selection`; the host must authorize the
exact selection before invoking it. All writes use KnowledgeWriter. Repeated
identical imports are idempotent; Work merging remains the writer's hard-ID policy.
The optional pure `group_results` function groups search views by DOI, PMID, arXiv,
then normalized title, rejects conflicting hard identifiers, and retains every
original record. It never performs a title-only Work merge.

PDF attachments require an allowlisted explicit open license, nonempty license
provenance, HTTPS, a declared PDF host, and a PDF signature. Denied or unavailable
PDFs leave a metadata-only import with an explanation. Arbitrary publisher PDF
hosts are intentionally not granted. The supplied PDF signature check does not
claim to sanitize PDF contents; host document parsing remains a trust boundary.

RIS and BibTeX accept UTF-8/UTF-8-BOM. RIS continuation lines are supported; missing
ER terminators are errors. CNKI RIS exports are accepted; proprietary RefWorks or
CAJ formats are not claimed. Parsing does not itself write or approve anything.

## Limits

Live upstream compatibility was not exercised. Search dedupe is an explicit helper,
not an undocumented global federated-search hook. Optional provider-specific filters,
PDF hosts and closed/proprietary file formats are not silently approximated.

## Installation and validation

Install into a PluginKit v2 / Research OS contracts 1.2 environment:

```sh
pip install dist/*.whl
ruff check src tests
ruff format --check src tests
mypy --strict src tests
pytest -q
```

Development: `pip install -e ".[dev]"`. The wheel does not include or modify core.
The distribution version is 0.1.0; the manifest requires `>=1.2,<2` contracts.
All test fixtures are original synthetic CC0-1.0 data, never real research evidence.
Tests block socket connections; provider calls use SDK FakeHttp exclusively.
No tools, routes, migrations, approval handlers or private core imports are registered.
The host owns installation approval, operation authorization and final publication.

Mypy follows the public core's unmarked Python sources with `follow_untyped_imports`
because this snapshot lacks a `py.typed` marker. `follow_imports="silent"` retains
imported type information while reporting errors only in this plugin. The standard
parser (`native_parser=false`) avoids a mypy 2.4 CPU-affinity crash in this execution
environment; no strict checks are disabled. Small typed adapters describe the
untyped third-party APIs used. Core files remain unchanged.

`MANIFEST.sha256` hashes every packaged file except itself (a file cannot include
its own ordinary SHA-256 digest). Check from the plugin root with
`sha256sum -c MANIFEST.sha256`. Wheels contain runtime sources, type markers and
any template resources; tests and fixtures remain in the source archive.
