# Research OS report_export 0.1.0

Three Exporter implementations: `report_docx`, `report_latex` and `report_slides`.
They accept typed ReportSnapshot report/answer/manuscript content and resolve all
CiteSpan and MetricSpan values through ExportContext, including table cells and captions.

## Outputs and options

DOCX: report.docx plus provenance.json.
LaTeX: report.tex, references.bib, provenance.json and verified PNG/JPEG figure files.
All filenames are fixed or derived from verified SHA-256 hashes, within ctx.out_dir.
Output symlinks that could escape the host directory are refused.

DOCX/LaTeX options (per-export options_schema; no global credentials):
`template`: lab_report (default), literature_review, short_paper.
`english_font`: Times New Roman; `chinese_font`: SimSun.
`font_size`: 8–24 (default 11); `margin_mm`: 10–50 (default 25).
`pdf`: false by default. True retains source outputs and emits `pdf_unavailable`;
this release never launches a local process without a public managed-process contract.

The short-paper layout is two-column and IEEE-like, not publisher certified.
Literature-review layout uses more paragraph spacing; lab report uses compact spacing.
The source's headings and section order are preserved; no scientific prose or results
are generated. DOCX sets Latin and eastAsia fonts. LaTeX targets XeLaTeX with fontspec,
xeCJK and the configured local fonts; fonts/packages must already be installed.
No font downloads, compilation or claims of font availability occur here.

## Evidence and failure semantics

Unresolved citation/metric, missing or hash-mismatched figure, nonfinite metric or
invalid table produces an error and **no newly declared outputs in final mode**.
Draft mode renders explicit placeholders and warning diagnostics. The host should
supply a fresh output directory; publication/rollback is a host responsibility.

Bibliography entries are deduplicated by DOI/PMID/arXiv or source identity. All original
citekeys, every EvidenceRef (ID, version, page, quote, trust), claim IDs, metric provenance
and artifact hashes/run IDs remain in provenance.json. The rendered references show
stable original citekeys; companion BibTeX uses deterministic safe hashed keys.
Unknown bibliography fields are omitted or labeled unavailable, never synthesized.

Literal text such as `{{cite:key}}` and numbers in TextSpan are retained verbatim;
only typed spans are resolved. This contract does not expose a semantic numeric-claim
validator, so exporters cannot certify arbitrary prose numbers as evidence-backed.

LaTeX/BibTeX text is escaped. Figures accept host-resolved PNG/JPEG artifacts no larger
than 5 MB with matching hashes; SVG, arbitrary paths/URLs and table-file artifacts
are unsupported and diagnosed. Structured table rows are supported directly.

## Limits

The exporter cannot validate a host's evidence snapshot freshness beyond the provided
resolver, or synthesize claim-to-evidence edges not represented in the snapshot.
No external fonts or LaTeX engine were exercised. DOCX semantic XML, Unicode/font
attributes, figure bytes, TeX escaping and evidence/metric consistency are tested offline.

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

## Slides (Feature 6, ADR-132)

`report_slides` emits `report.pptx` and `provenance.json` through the same snapshot,
resolver and host draft/final gates. It accepts no formatting options: backgrounds
are white, body text is 24 pt, headings are 28 pt, and each slide has at most ten
explicit lines including its heading. Text is conservatively wrapped by Unicode
width before splitting; overflow produces `slide_overflow` warnings in the export
result and sidecar, including final exports. Fonts are not downloaded or embedded;
viewer font substitution can affect visual rendering.

After the report title, sections appear as Background, Objectives, Method, Results,
Discussion, Conclusion. Case-insensitive exact section headings select their section;
other headings remain as text in the current section (initially Background).
Empty sections say “No content supplied.” No summaries or findings are invented.
Tables become readable text rows, verified figures occupy dedicated slides, and
captions/claim IDs follow in text slides. References use the shared deduplication.
The sidecar retains the original snapshot and all evidence and metric provenance.
