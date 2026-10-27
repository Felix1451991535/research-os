"""Plugin/template-facing SDK. Depends only on the standard library and researchos.contracts (import-linter enforced).
Templates use `RunContext.from_env()`; see runtime.py for the contract (ARCHITECTURE §6.2)."""

from .perf import LatencyStats, measure_latency
from .runtime import NonFiniteMetric, RunContext, SplitNotAvailable

__all__ = ["LatencyStats", "NonFiniteMetric", "RunContext", "SplitNotAvailable", "measure_latency"]
