"""Template SDK contract (ARCHITECTURE §6.2).

Run dir layout (prepared by the worker, never by the template):
  run.json        manifest written by the worker (seed, hashes, device, params)
  params.json     approved parameters for this run
  inputs/<split>.npz | inputs/<split>/...   only the splits allowed for this stage (test never before freeze)
  CANCEL          created by the worker to request a graceful stop
Template outputs:
  metrics.jsonl   {"kind":"metric","name","value","split","step","unit","t_wall"} | {"kind":"progress",...}
  summary.json    headline metrics {name, split, value, unit, verification}
  predictions/<split>.csv   sample_id,y_true,y_pred   (required for verification="recomputed")
  access.log      data accesses (one JSON line per ctx.data call)
"""

from __future__ import annotations

import csv
import json
import math
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

VERIFICATION_MODES = ("recomputed", "invariant_checked", "self_reported")


class NonFiniteMetric(RuntimeError):
    pass


class SplitNotAvailable(PermissionError):
    pass


class RunContext:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.params: dict[str, Any] = json.loads((run_dir / "params.json").read_text(encoding="utf-8"))
        manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.seed: int = int(manifest.get("seed", 0))
        self.stage: str = str(manifest.get("stage", "trial"))
        self.splits: list[str] = list(manifest.get("splits", []))
        self.checkpoint_dir = run_dir / "checkpoints"
        self.checkpoint_dir.mkdir(exist_ok=True)
        self._metrics = (run_dir / "metrics.jsonl").open("a", encoding="utf-8")
        self._summary: list[dict[str, Any]] = []

    @classmethod
    def from_env(cls) -> RunContext:
        return cls(Path(os.environ["ROS_RUN_DIR"]))

    # ---- data ----
    def data(self, split: str) -> Path:
        """Path of a materialized split. Splits not prepared for this stage do not exist on disk at all."""
        with (self.run_dir / "access.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"split": split, "t": time.time()}) + "\n")
        for cand in (self.run_dir / "inputs" / f"{split}.npz", self.run_dir / "inputs" / split):
            if cand.exists():
                return cand
        raise SplitNotAvailable(f"split '{split}' is not available in stage '{self.stage}'")

    def eval_split(self) -> str:
        """The evaluation split of this stage (search_val | confirm_val | test). Use this instead of probing:
        every ctx.data() call is logged, and asking for a split outside the stage is a provenance violation (D04)."""
        others = [s for s in self.splits if s != "train"]
        if not others:
            raise SplitNotAvailable(f"stage '{self.stage}' has no evaluation split")
        return others[0]

    # ---- telemetry ----
    def _write(self, rec: dict[str, Any]) -> None:
        self._metrics.write(json.dumps({**rec, "t_wall": time.time()}, allow_nan=True) + "\n")
        self._metrics.flush()

    def log_metric(
        self, name: str, value: float, *, split: str, step: int | None = None, unit: str | None = None
    ) -> None:
        v = float(value)
        self._write({"kind": "metric", "name": name, "value": v, "split": split, "step": step, "unit": unit})
        if not math.isfinite(v):
            raise NonFiniteMetric(f"{name}={v} at step {step}")

    def progress(self, stage: str, step: int | None = None, total: int | None = None) -> None:
        self._write({"kind": "progress", "stage": stage, "step": step, "total": total})

    def report(self, name: str, value: float, *, split: str, verification: str, unit: str | None = None) -> None:
        if verification not in VERIFICATION_MODES:
            raise ValueError(f"verification must be one of {VERIFICATION_MODES}")
        self._summary.append(
            {"name": name, "split": split, "value": float(value), "unit": unit, "verification": verification}
        )
        (self.run_dir / "summary.json").write_text(
            json.dumps(self._summary, allow_nan=True, indent=1), encoding="utf-8"
        )

    def save_predictions(
        self, split: str, sample_ids: Sequence[Any], y_true: Sequence[Any], y_pred: Sequence[Any]
    ) -> Path:
        out = self.run_dir / "predictions" / f"{split}.csv"
        out.parent.mkdir(exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["sample_id", "y_true", "y_pred"])
            w.writerows(zip(sample_ids, y_true, y_pred, strict=True))
        return out

    # ---- control ----
    def cancel_requested(self) -> bool:
        return (self.run_dir / "CANCEL").exists()

    def close(self) -> None:
        self._metrics.close()
