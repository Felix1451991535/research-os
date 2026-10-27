"""Inference-efficiency helpers for templates (stdlib only). Latency is wall-clock on the device the template ran on;
report it with split="device:<cpu|cuda>" so every number carries its device label. A host suspend during the run
(D06) voids timing metrics (unit "ms"/"s") at ingest."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class LatencyStats:
    p50_ms: float
    p95_ms: float
    mean_ms: float
    warmup: int
    repeats: int


def measure_latency(fn: Callable[[], object], *, warmup: int = 10, repeats: int = 100) -> LatencyStats:
    """Calls `fn` `warmup` times (discarded), then `repeats` times, timing each call individually.
    For GPU code, `fn` must synchronize (e.g. torch.cuda.synchronize()) before returning."""
    if warmup < 0 or repeats < 2:
        raise ValueError("warmup >= 0 and repeats >= 2 required")
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    q = statistics.quantiles(times, n=20, method="inclusive")
    return LatencyStats(statistics.median(times), q[18], statistics.fmean(times), warmup, repeats)
