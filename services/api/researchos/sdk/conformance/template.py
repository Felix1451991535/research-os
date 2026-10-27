"""Conformance for experiment templates (K5 + §6.2): valid manifest, runs in a prepared run dir, only touches
allowed splits, finite metrics, recomputable metrics have predictions on eval-split sample ids, honours CANCEL."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import jsonschema

from ..checks import template_import_violations
from ..runtime import VERIFICATION_MODES


class TemplateConformance:
    stage = "trial"
    eval_split = "search_val"
    timeout_s = 300.0

    def template_root(self) -> Path:
        raise NotImplementedError

    def params(self) -> dict[str, Any]:
        raise NotImplementedError

    def split_arrays(self) -> dict[str, dict[str, Any]]:
        """{"train": {"X":..., "y":..., "group":..., "sample_id":...}, "search_val": {...}} (numpy arrays)."""
        raise NotImplementedError

    def _manifest(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.template_root() / "template.json").read_text(encoding="utf-8"))
        return data

    def _prepare(self, run_dir: Path) -> None:
        import numpy as np

        (run_dir / "inputs").mkdir(parents=True)
        for split, arrays in self.split_arrays().items():
            np.savez(run_dir / "inputs" / f"{split}.npz", **arrays)
        (run_dir / "params.json").write_text(json.dumps(self.params()))
        (run_dir / "run.json").write_text(
            json.dumps({"seed": 0, "stage": self.stage, "splits": sorted(self.split_arrays())})
        )

    def _run(self, run_dir: Path, cancel_after_s: float | None = None) -> int:
        env = {**os.environ, "ROS_RUN_DIR": str(run_dir)}
        p = subprocess.Popen(
            [sys.executable, str(self.template_root() / self._manifest()["entry"])],
            cwd=run_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if cancel_after_s is not None:
            time.sleep(cancel_after_s)
            (run_dir / "CANCEL").touch()
        try:
            return p.wait(timeout=self.timeout_s)
        finally:
            if p.poll() is None:
                p.kill()

    def test_manifest(self) -> None:
        m = self._manifest()
        for k in ("id", "version", "entry", "params_schema"):
            assert k in m, f"template.json lacks {k}"
        assert (self.template_root() / m["entry"]).is_file()
        jsonschema.validate(self.params(), m["params_schema"])
        for metric in m.get("metrics", []):
            assert metric.get("verification", "self_reported") in VERIFICATION_MODES

    def test_self_contained(self) -> None:
        """The reviewed hash covers only the template dir: code outside it (e.g. the plugin package) is refused."""
        m = self._manifest()
        violations = template_import_violations(self.template_root(), m.get("requires", []))
        assert not violations, violations

    def test_requires_importable(self) -> None:
        """`requires` holds import names (e.g. "sklearn", not "scikit-learn"); the host checks them with find_spec."""
        import importlib.util

        missing = [r for r in self._manifest().get("requires", []) if importlib.util.find_spec(r) is None]
        assert not missing, f"requires entries are not importable module names here: {missing}"

    def test_run_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            self._prepare(rd)
            assert self._run(rd) == 0, "template must exit 0 on valid inputs"
            recs = [json.loads(line) for line in (rd / "metrics.jsonl").read_text().splitlines() if line.strip()]
            for r in recs:
                if r.get("kind") == "metric":
                    assert isinstance(r["value"], int | float) and r["value"] == r["value"], "metrics must be finite"
            access = (
                [json.loads(line)["split"] for line in (rd / "access.log").read_text().splitlines()]
                if (rd / "access.log").is_file()
                else []
            )
            assert not set(access) - set(self.split_arrays()), f"accessed splits outside the stage: {access}"
            summary = json.loads((rd / "summary.json").read_text()) if (rd / "summary.json").is_file() else []
            assert summary, "template must report at least one headline metric"
            eval_ids = {str(x) for x in self.split_arrays()[self.eval_split]["sample_id"]}
            for m in summary:
                if m["verification"] == "recomputed":
                    preds = list((rd / "predictions").glob(f"{m['split']}*.csv"))
                    assert preds, f"{m['name']} is 'recomputed' but no predictions were saved"
                    for p in preds:
                        ids = {line.split(",")[0] for line in p.read_text().splitlines()[1:]}
                        assert ids <= eval_ids, f"predictions in {p.name} contain ids outside {m['split']}"
