# Fixtures: all SYNTHETIC (CC0-1.0)

Hand-written test data shaped like Zotero exports and Zotero Web API v3 responses. No real library, no real
people, no API keys. DOIs use the Crossref test prefix `10.5555`; PMIDs/PMCIDs/arXiv ids are made up. ISBN
978-0-306-40615-7 / 0-306-40615-2 is the standard checksum example.

- `zotero_export.csl.json`: CSL-JSON export. Includes a DOI with a URL prefix, PMID/PMCID/arXiv in the Extra
  (`note`) field, a CJK title, a group-library book with two ISBNs, a local-library item, a citation-key id, an
  exact duplicate item and an item without a title (skipped).
- `zotero_export.rdf`: Zotero RDF export. Includes a nested journal, authors, tags (manual + automatic), a child
  note (HTML), an attachment, a CJK book referenced as the container of a book section, nested collections
  and an item without a title (skipped).
- `api_items_page1.json`, `api_items_page2.json`: two pages of `GET /users/1234567/items?itemKey=...` (format=json),
  served with `Link: rel="next"` and `Total-Results` headers by the tests.
