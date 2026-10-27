"""Pre-run gates for experiment runs. Each returns an error (code, message, state) or None."""

from __future__ import annotations

import importlib.util
import sqlite3
from typing import Any

import jsonschema

from ...kernel.db import Row, row
from ...sdk.checks import template_import_violations
from . import plans
from .templates import TemplateInfo

Gate = tuple[str, str, str] | None  # (error_code, message, "failed"|"blocked")


def approval_gate(conn: sqlite3.Connection, rev: Row, stage: str) -> Gate:
    if plans.approval_covers(conn, rev["plan_id"], int(rev["revision"])) is None:
        return "plan_not_approved", "this plan revision (or a widening change) has no valid approval", "blocked"
    if stage != "final_eval":
        return None
    subj = f"{rev['plan_id']}@{rev['revision']}"
    a = row(
        conn.execute(
            "SELECT * FROM approvals WHERE subject_type='final_eval' AND subject_id=? AND revoked_at IS "
            "NULL ORDER BY created_at DESC LIMIT 1",
            (subj,),
        )
    )
    if a is None or a["subject_hash"] != plans.final_eval_subject_hash(conn, subj):
        return "final_eval_not_approved", "final evaluation needs its own approval", "blocked"
    done = row(
        conn.execute(
            "SELECT r.id FROM runs r JOIN tasks t ON t.id=r.task_id WHERE t.kind='experiment.run' AND "
            "json_extract(t.payload,'$.plan_id')=? AND json_extract(t.payload,'$.stage')='final_eval' "
            "AND r.state='succeeded' LIMIT 1",
            (rev["plan_id"],),
        )
    )
    if done:
        return "final_eval_already_done", "the sealed test set was already evaluated once for this plan", "failed"
    return None


def template_gate(tpl: TemplateInfo | None, rev: Row) -> Gate:
    want = rev["content"]["template"]
    if tpl is None:
        return "template_missing", f"template {want['id']} not found", "blocked"
    if tpl.status != "reviewed" or tpl.hash != want["hash"]:
        return (
            "template_not_reviewed",
            f"template {tpl.manifest.id} is {tpl.status}; hash must match the approved plan and REVIEWED.json",
            "blocked",
        )
    violations = template_import_violations(tpl.path, tpl.manifest.requires)
    if violations:
        return "template_not_self_contained", "; ".join(violations[:3]), "blocked"
    missing = [m for m in tpl.manifest.requires if importlib.util.find_spec(m) is None]
    if missing:
        return "missing_dependency", f"template needs {missing}; install them (no CPU/GPU result is faked)", "failed"
    return None


def params_gate(tpl: TemplateInfo, rev: Row, params: dict[str, Any]) -> Gate:
    try:
        jsonschema.validate(params, tpl.manifest.params_schema)
    except jsonschema.ValidationError as e:
        return "invalid_params", e.message, "failed"
    content = rev["content"]
    space = content.get("space")
    if not space:
        if params != content.get("params", {}):
            return "params_not_approved", "params differ from the approved plan params", "blocked"
        return None
    fixed = content.get("params", {})
    for k, v in params.items():
        if k in fixed and fixed[k] != v:
            return "params_not_approved", f"{k} is fixed by the plan", "blocked"
        spec = space.get(k)
        if k not in fixed and spec is None:
            return "params_not_approved", f"{k} is outside the approved search space", "blocked"
        if spec and (
            "choices" in spec and v not in spec["choices"] or "low" in spec and not spec["low"] <= v <= spec["high"]
        ):
            return "params_not_approved", f"{k}={v} outside the approved range", "blocked"
    return None
