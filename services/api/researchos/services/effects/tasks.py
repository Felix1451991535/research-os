"""K2 task kinds for K4: apply / undo an approved plan. `late.svc` is the
live Services container (set by wiring)."""

from __future__ import annotations

from typing import Any

from ...contracts.errors import ApiError
from ...contracts.tasks import ResourceClass, TaskContext, TaskKindSpec, TaskOutcome, TaskState
from ...kernel.db import connect
from . import apply as effects_apply

PLAN_SCHEMA = {"type": "object", "required": ["plan_id"], "properties": {"plan_id": {"type": "string"}},
               "additionalProperties": False}


def _plan_kind(name: str, desc: str, fn: Any, late: Any) -> TaskKindSpec:
    def run(ctx: TaskContext) -> TaskOutcome:
        conn = connect(late.svc.settings.db_path)
        try:
            res = fn(conn, str(ctx.payload["plan_id"]), f"task:{ctx.task_id}")
        except ApiError as e:
            return TaskOutcome(TaskState.FAILED, error_code=e.code, error_message=e.message)
        finally:
            conn.close()
        ok = res["state"] in ("applied", "undone")
        return TaskOutcome(TaskState.SUCCEEDED if ok else TaskState.FAILED, res,
                           None if ok else f"effect_{res['state']}", None if ok else str(res.get("reason") or res))

    return TaskKindSpec(name, desc, ResourceClass.LIGHT, payload_schema=PLAN_SCHEMA, handler=run)


def effect_kinds(late: Any) -> list[TaskKindSpec]:
    return [_plan_kind("effects.apply", "apply an approved EffectPlan (journaled)", effects_apply.apply, late),
            _plan_kind("effects.undo", "undo an applied EffectPlan from its journal", effects_apply.undo, late)]
