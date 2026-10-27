"""TemplateConformance for physio_ecg_baseline on tables produced by the providers (SYNTHETIC upstream data),
split exactly as the host would: by the provider's patient-level split spec."""

from __future__ import annotations

import json
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
from researchos.sdk.conformance import TemplateConformance
from researchos.sdk.testing import FakeBuildContext

from researchos_plugin_physio_signals import mitbih, ptbxl, template_package
from synth import CC_BY, MITDB, PTBXL, mitdb_files, ptbxl_files, tree_http
from test_providers import MIT_SETTINGS


def _materialize(provider: Any, http: Any, settings: dict[str, Any], splits: dict[str, str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        d = provider.build(FakeBuildContext(Path(td), http), settings)
        with np.load(Path(td) / d.table, allow_pickle=False) as t:
            data = {k: t[k] for k in t.files}
    assign = d.extra["split"]["assign"]
    out: dict[str, Any] = {}
    for name, source in splits.items():
        rows = np.isin(data["group"], assign[source])
        out[name] = {k: v[rows] for k, v in data.items()}
    return out


@cache
def mit_splits() -> dict[str, Any]:
    return _materialize(
        mitbih.MitBihProvider(),
        tree_http(MITDB, mitdb_files()),
        dict(MIT_SETTINGS),
        {"train": "train", "search_val": "search_val"},
    )


@cache
def ptb_splits() -> dict[str, Any]:
    return _materialize(
        ptbxl.PtbXlProvider(),
        tree_http(PTBXL, ptbxl_files(), license_text=CC_BY),
        {},
        {"train": "train", "test": "test"},
    )


class TestEcgBaselineMitBih(TemplateConformance):
    def template_root(self) -> Path:
        return template_package().root

    def params(self) -> dict[str, Any]:
        return {"model": "logreg", "C": 1.0, "class_weight": "balanced", "morph_points": 16}

    def split_arrays(self) -> dict[str, dict[str, Any]]:
        return mit_splits()

    def test_reports_per_class_metrics_and_confusion(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            self._prepare(rd)
            assert self._run(rd) == 0
            names = {
                json.loads(x)["name"]
                for x in (rd / "metrics.jsonl").read_text().splitlines()
                if json.loads(x)["kind"] == "metric"
            }
            assert "macro_f1" in names and "sensitivity_c0" in names and "ppv_c0" in names
            assert any(n.startswith("confusion_true") for n in names)
            cm = json.loads((rd / "confusion_matrix.json").read_text())
            assert sum(map(sum, cm["rows_true_cols_pred"])) == len(self.split_arrays()["search_val"]["y"])
            access = [json.loads(x)["split"] for x in (rd / "access.log").read_text().splitlines()]
            assert access == ["train", "search_val"]

    def test_deterministic_under_same_seed(self) -> None:
        outs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as td:
                rd = Path(td)
                self._prepare(rd)
                assert self._run(rd) == 0
                outs.append((rd / "predictions" / "search_val.csv").read_text())
        assert outs[0] == outs[1]

    def test_honours_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rd = Path(td)
            self._prepare(rd)
            (rd / "CANCEL").touch()
            assert self._run(rd) == 0
            assert not (rd / "summary.json").exists() and not (rd / "predictions").exists()


class TestEcgBaselinePtbXlRandomForest(TemplateConformance):
    stage = "final_eval"
    eval_split = "test"

    def template_root(self) -> Path:
        return template_package().root

    def params(self) -> dict[str, Any]:
        return {"model": "rf", "n_estimators": 20, "max_depth": 4}

    def split_arrays(self) -> dict[str, dict[str, Any]]:
        return ptb_splits()


def test_no_reviewed_json_shipped() -> None:
    assert not list(template_package().root.rglob("REVIEWED.json"))
