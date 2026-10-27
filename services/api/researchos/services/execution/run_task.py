"""Task kind `experiment.run`: gates -> RunBundle -> supervised process -> detectors -> ingest -> outcome.
Payload: {plan_id, revision, stage: trial|confirm|final_eval|smoke, params, seed}."""

from __future__ import annotations

from typing import Any

from ...contracts.execution import STAGE_SPLITS
from ...contracts.tasks import ResourceClass, TaskContext, TaskKindSpec, TaskOutcome, TaskState
from ...kernel.config import Settings
from ...kernel.db import connect, jload, row, tx
from ...kernel.events import emit
from ..orchestration.leases import record_child
from ..verification.registry import run_all
from ..verification.view import DirRunView
from . import bundle as bundle_mod
from . import datasets, gates, plans, run_figures
from .ingest import ingest
from .local_executor import LocalProcessExecutor
from .monitor import Monitor
from .templates import TemplateRegistry

SCHEMA = {
    "type": "object",
    "required": ["plan_id", "revision", "stage", "params", "seed"],
    "properties": {
        "plan_id": {"type": "string"},
        "revision": {"type": "integer"},
        "stage": {"enum": ["trial", "confirm", "final_eval", "smoke"]},
        "params": {"type": "object"},
        "seed": {"type": "integer"},
        # study bookkeeping (K2 children of a study) and the negative-control flag
        "study_id": {"type": "string"},
        "tag": {"type": "string"},
        "point": {"type": "integer"},
        "center": {"type": "boolean"},
        "control": {"enum": ["shuffled_labels"]},
    },
    "additionalProperties": False,
    # a negative control never runs outside the trial stage, whichever route created the task (review #74)
    "if": {"required": ["control"]},
    "then": {"properties": {"stage": {"const": "trial"}}},
}


def _fail(code: str, msg: str, state: str = "failed") -> TaskOutcome:
    return TaskOutcome(
        TaskState.BLOCKED if state == "blocked" else TaskState.FAILED, error_code=code, error_message=msg
    )


def experiment_kind(settings: Settings, registry: TemplateRegistry) -> TaskKindSpec:
    def run(ctx: TaskContext) -> TaskOutcome:
        conn = connect(settings.db_path)
        try:
            return _run(conn, ctx, settings, registry)
        finally:
            conn.close()

    return TaskKindSpec(
        "experiment.run",
        "run one reviewed template under an approved plan",
        ResourceClass.CPU,
        payload_schema=SCHEMA,
        handler=run,
    )


def catalog_revision(conn: Any) -> int:
    r = row(conn.execute("SELECT value_json FROM settings_kv WHERE key='catalog.revision'"))
    return int(jload(r["value_json"])) if r else 0


def _run(conn: Any, ctx: TaskContext, settings: Settings, registry: TemplateRegistry) -> TaskOutcome:
    p = ctx.payload
    if p.get("control") and p["stage"] != "trial":  # re-checked at claim time (review #74)
        return _fail("bad_control", "a negative control runs at the trial stage only", "failed")
    rev = plans.get_revision(conn, p["plan_id"], int(p["revision"]))
    catalog_rev = catalog_revision(conn)  # revision fence: observed once, at the start of this run
    tpl = registry.get(rev["content"]["template"]["id"])  # registry snapshot for this run
    for g in (gates.approval_gate(conn, rev, p["stage"]), gates.template_gate(tpl, rev)):
        if g:
            return _fail(*g)
    assert tpl is not None
    g = gates.params_gate(tpl, rev, p["params"])
    if g:
        return _fail(*g)
    ds_ref = rev["content"].get("dataset")
    dataset = datasets.get(conn, ds_ref["id"]) if ds_ref else None
    if dataset and dataset["manifest_sha256"] != ds_ref.get("manifest_sha256"):
        return _fail("dataset_changed", "dataset manifest differs from the approved plan", "blocked")
    b, mat = bundle_mod.prepare(
        settings, ctx.run_id, tpl, rev, p["stage"], p["params"], int(p["seed"]), dataset, catalog_rev,
        control=p.get("control"),
    )
    with tx(conn):
        conn.execute(
            "UPDATE runs SET catalog_revision=?, run_dir=?, device_label=? WHERE id=?",
            (catalog_rev, b.run_dir.relative_to(settings.data_dir).as_posix(), b.device_label, ctx.run_id),
        )
    ex = LocalProcessExecutor()
    h = ex.start(b)
    record_child(conn, ctx.run_id, h.child.pid, h.child.create_time, b.device_label)
    facts = Monitor(ex, h, b, ctx, settings.cancel_grace_s).run()
    extra = {**facts.as_extra(), "materialized": mat, "allowed_splits": list(STAGE_SPLITS[p["stage"]])}
    ids = {k: set(v) for k, v in mat.get("ids", {}).items()}
    view = DirRunView(b.run_dir, _template_dict(tpl), ids, extra)
    signals = run_all(view)
    figs = run_figures.draw(b.run_dir, tpl.manifest.task, list(STAGE_SPLITS[p["stage"]]))
    metrics = ingest(conn, settings.data_dir, ctx.run_id, view, signals)
    with tx(conn):
        emit(
            conn,
            "run.finished",
            actor=f"task:{ctx.task_id}",
            entity_type="run",
            entity_id=ctx.run_id,
            data={
                "exit_code": facts.exit_code,
                "killed": facts.killed_reason,
                "signals": [f"{s.detector}:{s.severity}" for s in signals],
                "figures": figs,
            },
        )
    return _outcome(facts, signals, metrics, b.run_dir.name)


def _template_dict(tpl: Any) -> dict[str, Any]:
    m = tpl.manifest
    return {"id": m.id, "metrics": list(m.metrics), "task": m.task}


def _outcome(facts: Any, signals: list[Any], metrics: list[dict[str, Any]], run_dir: str) -> TaskOutcome:
    result = {
        "run_dir": run_dir,
        "metrics": metrics,
        "signals": [s.__dict__ for s in signals],
        "killed": facts.killed_reason,
    }
    if facts.termination_failed:  # checked first: a surviving process is never a clean cancel or success
        return TaskOutcome(
            TaskState.FAILED,
            result,
            error_code="termination_failed",
            error_message=f"process (or a descendant) still running after kill ({facts.killed_reason or 'exit'}); "
            "stop it manually (the run records its pid)",
        )
    if facts.cancelled:
        return TaskOutcome(TaskState.CANCELLED, result)
    fails = [s for s in signals if s.severity == "fail"]
    if fails:
        return TaskOutcome(TaskState.FAILED, result, error_code=fails[0].detector, error_message=fails[0].rule)
    if facts.killed_reason:
        return TaskOutcome(TaskState.FAILED, result, error_code=facts.killed_reason, error_message="run stopped")
    return TaskOutcome(TaskState.SUCCEEDED, result)
