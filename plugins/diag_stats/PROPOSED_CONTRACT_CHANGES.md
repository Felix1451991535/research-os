# Proposed contract changes (contracts 1.2 -> 1.3); core was NOT patched

Everything requested could be implemented with contracts 1.2. The items below are improvements, not blockers.

## 1. Structured tool errors
`ToolBinding.impl` can only return a string or raise. Invalid input raises `StatsInputError` (a ValueError).
How the AgentRun surfaces exceptions to the model is host-defined. Proposal: a `ToolError(code, message)`
exception in `contracts.gateway` that the host turns into a tool-result error for the model.
Acceptance: ToolConformance gains `sample_invalid_args()` and checks that ToolError is raised.

## 2. Host truncation of tool output
`gateway.tools.execute` cuts the output at `max_output_bytes`, which turns JSON into invalid JSON. The plugin
bounds input sizes in the schemas and raises an error instead of exceeding the limit. Proposal: the host
should reject (not truncate) oversized output, or ToolSpec should declare `output_format: "json"`.

## 3. Metric uncertainty in `MetricValue`
`MetricValue` has `value`, `unit` and `verification` only, so the exporter cannot render CIs, n or the method
next to a number unless each bound is a separate metric. Proposal: optional `ci: tuple[float, float] | None`,
`n: int | None`, `method: str | None`. Acceptance: ExporterConformance checks that CI bounds are rendered when
present.

## 4. Verification label for CIs
`RunContext.report(verification=...)` knows `recomputed | invariant_checked | self_reported`. A bootstrap CI is
reproducible (seed + resamples) but cannot be "recomputed" from saved predictions by the host as-is.
Proposal: allow the host to recompute registered tool results (tool name + args) as `recomputed`.
