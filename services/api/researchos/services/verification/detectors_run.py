"""Run-level detectors that use monitor facts (exit code, idle time, host suspend) and template declarations."""

from __future__ import annotations

from dataclasses import dataclass

from ...contracts.verification import HealthSignal
from .detectors import _metrics, _sig
from .view import DirRunView, finite


@dataclass(frozen=True)
class D05Stall:
    id: str = "stall"
    version: int = 1
    streaming: bool = True

    def check(self, v: DirRunView) -> list[HealthSignal]:
        idle = v.extra.get("stalled_idle_s")
        if idle:
            return [_sig("stall@1", "fail", "no progress beyond the stall threshold", {"idle_s": round(idle, 1)})]
        return []


@dataclass(frozen=True)
class D06HostSuspend:
    id: str = "host_suspend"
    version: int = 1
    streaming: bool = True

    def check(self, v: DirRunView) -> list[HealthSignal]:
        return [
            _sig(
                "host_suspend@1",
                "info",
                "host suspend detected: timing metrics in this run are invalid",
                {"gap_s": round(g, 1)},
            )
            for g in v.extra.get("suspend_gaps", [])
        ]


@dataclass(frozen=True)
class D08Exit:
    id: str = "exit_solver"
    version: int = 1
    streaming: bool = False

    def check(self, v: DirRunView) -> list[HealthSignal]:
        code = v.extra.get("exit_code")
        if code not in (0, None) and not v.extra.get("cancelled"):
            return [
                _sig(
                    "exit_solver@1",
                    "fail",
                    "template process exited with an error",
                    {"exit_code": code, "stderr_tail": v.extra.get("stderr_tail", "")[-400:]},
                )
            ]
        bad = [r for r in v.metric_records() if r.get("kind") == "solver" and not r.get("converged", True)]
        return [_sig("exit_solver@1", "fail", "solver reported non-convergence", {"count": len(bad)})] if bad else []


@dataclass(frozen=True)
class D09PlausibleRange:
    id: str = "plausible_range"
    version: int = 1
    streaming: bool = False

    def check(self, v: DirRunView) -> list[HealthSignal]:
        ranges = {m["name"]: m["range"] for m in v.template.get("metrics", []) if m.get("range")}
        out = []
        for m in v.summary():
            r = ranges.get(m["name"]) or ranges.get(m["name"].rsplit(".", 1)[-1])
            if r and finite(m.get("value")) and not (r[0] <= float(m["value"]) <= r[1]):
                out.append(
                    _sig(
                        "plausible_range@1",
                        "warn",
                        "outside the declared plausible range (flag, not fail)",
                        {"metric": m["name"], "value": m["value"], "range": r},
                    )
                )
        return out


@dataclass(frozen=True)
class D10ChanceLevel:
    id: str = "chance_level"
    version: int = 1
    streaming: bool = False
    eps: float = 0.02

    def check(self, v: DirRunView) -> list[HealthSignal]:
        n = v.template.get("task", {}).get("n_classes")
        if not n:
            return []
        out = []
        for m in v.summary():
            if (
                m["name"].endswith(("macro_f1", "accuracy"))
                and finite(m.get("value"))
                and float(m["value"]) <= 1 / n + self.eps
            ):
                out.append(
                    _sig(
                        "chance_level@1",
                        "warn",
                        "metric at chance level: model is not learning",
                        {"metric": m["name"], "value": m["value"], "chance": round(1 / n, 4)},
                    )
                )
        return out


def seed_spread(values: list[float], cv_threshold: float = 0.1) -> HealthSignal | None:
    """D11 is evaluated across runs of one configuration (sweep level): coefficient of variation across seeds."""
    vals = [x for x in values if finite(x)]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    sd = (sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5
    cv = sd / abs(mean) if mean else float("inf")
    return _sig("seed_spread@1", "warn", "unstable across seeds", {"cv": round(cv, 4)}) if cv > cv_threshold else None


def streaming_nonfinite(records: list[dict[str, object]]) -> bool:
    return any(r.get("kind") == "metric" and not finite(r.get("value")) for r in records)


__all__ = [
    "D05Stall",
    "D06HostSuspend",
    "D08Exit",
    "D09PlausibleRange",
    "D10ChanceLevel",
    "seed_spread",
    "streaming_nonfinite",
    "_metrics",
]
