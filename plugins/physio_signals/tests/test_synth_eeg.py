"""physio_synth_eeg (ADR-187): SYNTHETIC EEG whose planted truth the pipeline must recover."""

from __future__ import annotations

import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression

from researchos_plugin_physio_signals import template_package
from researchos_plugin_physio_signals.synth_eeg import SynthEegProvider
from test_template import FakeBuildContext

sys.path.insert(0, str(template_package().root))
import cards  # noqa: E402
import eeg  # noqa: E402

SETTINGS = {"subjects": 16, "seed": 3}


def _build(settings: dict[str, Any]) -> tuple[Any, dict[str, Any], str]:
    with tempfile.TemporaryDirectory() as td:
        d = SynthEegProvider().build(FakeBuildContext(Path(td), None), settings)
        with np.load(Path(td) / d.table, allow_pickle=False) as t:
            data = {k: t[k] for k in t.files}
    return d, data, d.files[0].sha256


@cache
def built() -> tuple[Any, dict[str, Any], str]:
    return _build(SETTINGS)


def test_deterministic_labelled_and_open() -> None:
    d, _, sha = built()
    assert _build(SETTINGS)[2] == sha and _build({**SETTINGS, "seed": 4})[2] != sha
    assert d.extra["synthetic"] and "not real" in d.extra["dataset"] and d.egress == "cloud_ok"
    assert d.extra["known_truth"]["non_responders"] and d.extra["known_truth"]["responders"]


def test_pipeline_recovers_planted_responders() -> None:
    """Train CSP + logistic regression on the train subjects; held-out responders decode, non-responders do not."""
    d, t, _ = built()
    truth, assign = d.extra["known_truth"], d.extra["split"]["assign"]
    tr = np.isin(t["group"], assign["train"])
    part = [{k: v[m] for k, v in t.items()} for m in (tr, ~tr)]
    x_tr, x_ev, _, _, _ = eeg.csp(part[0], part[1], eeg.csp_config({}))
    pred = LogisticRegression().fit(x_tr, part[0]["y"]).predict(x_ev)
    acc = {s: v["balanced_accuracy"] for s, v in cards.per_subject(part[1]["y"], pred, part[1]["group"]).items()}
    resp = [a for s, a in acc.items() if s in truth["responders"]]
    non = [a for s, a in acc.items() if s in truth["non_responders"]]
    assert resp and non  # this seed holds out both kinds
    assert min(resp) > 0.85 and max(non) < 0.7


def test_planted_artifacts_are_found_exactly() -> None:
    d, t, _ = built()
    high, _ = eeg.artifacts(np.asarray(t["X"], np.float64), 150.0)
    planted = np.isin(t["sample_id"], d.extra["known_truth"]["artifact_sample_ids"])
    assert planted.any() and np.array_equal(high, planted)


def test_zero_effect_is_not_decodable() -> None:
    d, t, _ = _build({**SETTINGS, "erd": 0.0})
    tr = np.isin(t["group"], d.extra["split"]["assign"]["train"])
    part = [{k: v[m] for k, v in t.items()} for m in (tr, ~tr)]
    x_tr, x_ev, _, _, _ = eeg.csp(part[0], part[1], eeg.csp_config({}))
    pred = LogisticRegression().fit(x_tr, part[0]["y"]).predict(x_ev)
    assert abs(float(np.mean(pred == part[1]["y"])) - 0.5) < 0.15  # no planted effect -> chance
