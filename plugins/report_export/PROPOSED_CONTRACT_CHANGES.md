# Proposed additive contract changes

Exporter, ReportSnapshot, structured spans, evidence, artifact and metric resolution
are present in 1.2 and used by both functional exporters.

1. No capability-scoped process/LaTeX execution contract exists. Add a managed
   process context with disabled shell escape, resource limits, cancellation,
   controlled fonts, bounded diagnostics and typed absent-engine/failure results.
   PDF requests currently return source files plus an explicit pdf_unavailable
   diagnostic; no process or network workaround is used.
2. TextSpan has no typed numeric-claim/evidence-validation status. The exporter
   resolves MetricSpan and retains literal text; it cannot identify arbitrary
   unsupported numeric prose. Add validated claim spans if final exports must
   reject every unsupported numeric claim, not just unresolved typed metrics.
3. Claim IDs and CitationEntry evidence are available, but no immutable per-claim
   evidence edge graph or freshness token is provided. Add these to support stronger
   claim-to-evidence and stale-snapshot checks. Current sidecars preserve all supplied
   structures faithfully, without inventing edges or missing evidence.
4. ExportContext has no transactional output commit or local font inventory.
   Publication/rollback and missing-font diagnostics require host services; currently
   final resolution errors return no outputs and the host controls the output root.
5. Publish core's py.typed marker for external strict type checking.

PNG/JPEG figures and structured table blocks are supported; arbitrary SVG and table
artifact payloads are not silently converted. PDF and installed-font visual fidelity
are explicitly not claimed by this offline release.
