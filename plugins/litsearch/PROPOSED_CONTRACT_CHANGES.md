# Proposed additive contract changes

The v1 blockers for BibliographicSource, Importer and FileImporter are resolved in 1.2.
This plugin implements all five planned API adapters, selected import, RIS/BibTeX
parsing and provenance-preserving result-view dedupe. No ToolBinding is necessary.

Remaining public-surface gaps:

1. SearchPage cannot represent a federated group with per-field provenance/conflicts.
   Add an optional result-group model if the host wants integrated cross-provider
   dedupe. The pure helper retains the original BibRecord objects without shadowing
   a core contract or merging Works by title.
2. HttpContext has no plugin policy setter, fake clock or cancellation/retry metadata
   access. Rate limits, Retry-After, caching, SSRF and key-redaction guarantees must
   be tested and configured by the host. Plugins neither reimplement nor claim to
   test these private host internals.
3. The core wheel lacks a PEP 561 py.typed marker. Add it so external strict typing
   follows the annotated public surface without follow_untyped_imports.

FileImporter has no SDK suite; PLUGIN_DEV explicitly specifies plain pytest. Both
registered file parsers have direct offline tests, not fake conformance claims.
Additional publisher PDF hosts, proprietary CNKI formats and provider filters are
release scope limits, not missing types. Runtime upstream availability is unverified.
