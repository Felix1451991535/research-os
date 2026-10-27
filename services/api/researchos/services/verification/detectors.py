"""Run-health detectors D01-D11 (ARCHITECTURE §6.3). Deterministic, versioned, read-only; output HealthSignal only.
Thresholds come from the template manifest / plan limits when present, else the defaults below."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from ...contracts.verification import HealthSignal
from .recompute import RECOMPUTERS, TOLERANCE, base_name
from .view import DirRunView, finite

LOSS_NAMES = ("loss", "train_loss")


def _sig(
    det: str, sev: str, rule: str, value: dict[str, Any] | None = None, selector: dict[str, Any] | None = None
) -> HealthSignal:
    return HealthSignal(det, sev, rule, value or {}, selector or {})  # type: ignore[arg-type]


def _metrics(v: DirRunView) -> list[dict[str, Any]]:
    return [r for r in v.metric_records() if r.get("kind") == "metric"]


@dataclass(frozen=True)
class D01NonFinite:
    id: str = "nonfinite"
    version: int = 1
    streaming: bool = True

    def check(self, v: DirRunView) -> list[HealthSignal]:
        for r in _metrics(v):
            if not finite(r.get("value")):
                return [
                    _sig(
                        "nonfinite@1",
                        "fail",
                        "any logged metric must be finite",
                        {"value": str(r.get("value"))},
                        {"metric": r.get("name"), "step": [r.get("step"), r.get("step")]},
                    )
                ]
        return []


@dataclass(frozen=True)
class D02Divergence:
    id: str = "divergence"
    version: int = 1
    streaming: bool = False
    warmup: int = 2
    factor: float = 2.0
    consecutive: int = 3

    def check(self, v: DirRunView) -> list[HealthSignal]:
        out: list[HealthSignal] = []
        for name in LOSS_NAMES:
            series = [float(r["value"]) for r in _metrics(v) if r.get("name") == name and finite(r.get("value"))]
            run, best = 0, math.inf
            for i, x in enumerate(series):
                best = min(best, x)
                run = run + 1 if i >= self.warmup and x > self.factor * best else 0
                if run >= self.consecutive:
                    out.append(
                        _sig(
                            "divergence@1",
                            "fail",
                            f"{name} > {self.factor}x running min for "
                            f"{self.consecutive} points after {self.warmup} warm-up points",
                            {"ratio": round(x / best, 3) if best > 0 else None},
                            {"metric": name, "step": [i - self.consecutive + 1, i]},
                        )
                    )
                    break
        return out


@dataclass(frozen=True)
class D02bOverfit:
    id: str = "overfit"
    version: int = 1
    streaming: bool = False

    def check(self, v: DirRunView) -> list[HealthSignal]:
        tr = [float(r["value"]) for r in _metrics(v) if r.get("name") == "train_loss" and finite(r.get("value"))]
        va = [float(r["value"]) for r in _metrics(v) if r.get("name") == "val_loss" and finite(r.get("value"))]
        if len(tr) >= 4 and len(va) >= 4 and all(va[-i] > va[-i - 1] for i in (1, 2, 3)) and tr[-1] < tr[-4]:
            return [_sig("overfit@1", "warn", "val loss rising for 3 points while train loss falls")]
        return []


def _half_hashes(path: Any) -> set[str]:
    import numpy as np

    with np.load(path, allow_pickle=False) as npz:
        x = npz["X"]
    if x.ndim < 2 or x.shape[1] < 2:
        return set()
    h = x.shape[1] // 2
    return {hashlib.sha256(np.ascontiguousarray(w).tobytes()).hexdigest() for row in x for w in (row[:h], row[h:])}


@dataclass(frozen=True)
class D03SplitIntegrity:
    id: str = "split_integrity"
    version: int = 1
    streaming: bool = False

    def check(self, v: DirRunView) -> list[HealthSignal]:
        info = v.extra.get("materialized", {})
        out: list[HealthSignal] = []
        groups = info.get("groups", {})
        names = sorted(groups)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                shared = set(groups[a]) & set(groups[b])
                if shared:
                    out.append(
                        _sig(
                            "split_integrity@1",
                            "fail",
                            f"{a} and {b} share groups",
                            {"shared_groups": sorted(shared)[:10]},
                        )
                    )
        if info.get("overlapping_windows"):
            inputs = v.run_dir / "inputs"
            files = sorted(inputs.glob("*.npz"))
            hashes = {f.stem: _half_hashes(f) for f in files}
            keys = sorted(hashes)
            for i, a in enumerate(keys):
                for b in keys[i + 1 :]:
                    n = len(hashes[a] & hashes[b])
                    if n:
                        out.append(
                            _sig(
                                "split_integrity@1",
                                "fail",
                                "half-window hashes shared across splits (overlapping windows leak)",
                                {"splits": [a, b], "shared_half_hashes": n},
                            )
                        )
        return out


@dataclass(frozen=True)
class D04SampleProvenance:
    id: str = "sample_provenance"
    version: int = 1
    streaming: bool = False

    def check(self, v: DirRunView) -> list[HealthSignal]:
        out: list[HealthSignal] = []
        allowed = set(v.extra.get("allowed_splits", []))
        bad = sorted({s for s in v.accessed_splits() if s not in allowed})
        if bad:
            out.append(
                _sig(
                    "sample_provenance@1",
                    "fail",
                    "template requested splits not allowed in this stage",
                    {"splits": bad},
                )
            )
        for key in v.prediction_keys():
            split = key.split("__", 1)[0]
            ids = v.split_ids(split)
            if ids is None:
                continue
            got = {r["sample_id"] for r in v.predictions(key) or []}
            foreign = got - ids
            if foreign:
                out.append(
                    _sig(
                        "sample_provenance@1",
                        "fail",
                        f"predictions for '{split}' contain samples outside that split",
                        {"key": key, "foreign_ids": len(foreign)},
                    )
                )
        return out


@dataclass(frozen=True)
class D07MetricRecompute:
    id: str = "metric_recompute"
    version: int = 1
    streaming: bool = False

    def check(self, v: DirRunView) -> list[HealthSignal]:
        out: list[HealthSignal] = []
        for m in v.summary():
            if m.get("verification") != "recomputed":
                continue
            fn = RECOMPUTERS.get(base_name(m["name"]))
            key = m["name"].rsplit(".", 1)[0] if "." in m["name"] else ""
            preds = v.predictions(f"{m['split']}__{key}" if key else m["split"])
            if fn is None or preds is None:
                out.append(
                    _sig(
                        "metric_recompute@1",
                        "warn",
                        "metric declared 'recomputed' but cannot be recomputed",
                        {"metric": m["name"]},
                    )
                )
                continue
            rec = fn(preds)
            if not finite(m.get("value")) or abs(float(m["value"]) - rec) > TOLERANCE:
                out.append(
                    _sig(
                        "metric_recompute@1",
                        "fail",
                        "reported metric differs from recomputation",
                        {"metric": m["name"], "reported": m.get("value"), "recomputed": round(rec, 6)},
                    )
                )
        return out
