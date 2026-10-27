"""Detector registry (entry-point group researchos.detectors adds more). Built-ins D01-D10; D11 at sweep level."""

from __future__ import annotations

from typing import Any

from ...contracts.verification import HealthSignal
from .detectors import (
    D01NonFinite,
    D02bOverfit,
    D02Divergence,
    D03SplitIntegrity,
    D04SampleProvenance,
    D07MetricRecompute,
)
from .detectors_run import D05Stall, D06HostSuspend, D08Exit, D09PlausibleRange, D10ChanceLevel
from .view import DirRunView

BUILTIN: tuple[Any, ...] = (
    D01NonFinite(),
    D02Divergence(),
    D02bOverfit(),
    D03SplitIntegrity(),
    D04SampleProvenance(),
    D05Stall(),
    D06HostSuspend(),
    D07MetricRecompute(),
    D08Exit(),
    D09PlausibleRange(),
    D10ChanceLevel(),
)


def run_all(view: DirRunView, detectors: tuple[Any, ...] = BUILTIN) -> list[HealthSignal]:
    out: list[HealthSignal] = []
    for d in detectors:
        try:
            out += d.check(view)
        except Exception as e:  # a broken detector is itself a finding, never a silent pass
            out.append(HealthSignal(f"{d.id}@{d.version}", "warn", "detector crashed", {"error": str(e)[:200]}))
    return out
