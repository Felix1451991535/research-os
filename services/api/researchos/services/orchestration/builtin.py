"""Core task kinds. `system.sleep` exists for health checks, fault-injection tests and selftest.ps1;
`system.guarded` demonstrates the approval gate; `system.maintenance` purges expired idempotency rows."""
from __future__ import annotations

import time

from ...contracts.tasks import ResourceClass, RetryPolicy, TaskContext, TaskKindSpec, TaskOutcome, TaskState
from ...kernel.config import Settings
from ...kernel.db import connect, tx
from ...kernel.idempotency import purge_expired

SLEEP_SCHEMA = {"type": "object", "properties": {"seconds": {"type": "number", "minimum": 0, "maximum": 3600},
                                                  "fail": {"type": "boolean"}},
                "required": ["seconds"], "additionalProperties": False}


def _sleep(ctx: TaskContext) -> TaskOutcome:
    total = float(ctx.payload["seconds"])
    start = time.monotonic()
    while time.monotonic() - start < total:
        if ctx.cancel_requested():
            return TaskOutcome(TaskState.CANCELLED, {"slept_s": round(time.monotonic() - start, 2)})
        if not ctx.check_lease():
            return TaskOutcome(TaskState.FAILED, error_code="lease_lost")
        ctx.progress("sleeping", {"elapsed_s": round(time.monotonic() - start, 1), "total_s": total})
        time.sleep(min(0.1, total))
    if ctx.payload.get("fail"):
        return TaskOutcome(TaskState.FAILED, error_code="requested_failure", error_message="payload.fail=true")
    return TaskOutcome(TaskState.SUCCEEDED, {"slept_s": total})


def maintenance_kind(settings: Settings) -> TaskKindSpec:
    def run(ctx: TaskContext) -> TaskOutcome:
        conn = connect(settings.db_path)
        try:
            with tx(conn):
                n = purge_expired(conn, settings.idempotency_ttl_days)
        finally:
            conn.close()
        return TaskOutcome(TaskState.SUCCEEDED, {"idempotency_purged": n})
    return TaskKindSpec("system.maintenance", "purge expired idempotency records", retry=RetryPolicy.IDEMPOTENT,
                        max_attempts=3, handler=run, payload_schema={"type": "object"})


def core_kinds(settings: Settings) -> list[TaskKindSpec]:
    return [
        TaskKindSpec("system.sleep", "sleep N seconds (health check / tests)", ResourceClass.CPU,
                     payload_schema=SLEEP_SCHEMA, handler=_sleep),
        TaskKindSpec("system.sleep_retryable", "idempotent sleep (lease-loss requeue demo)", ResourceClass.CPU,
                     RetryPolicy.IDEMPOTENT, max_attempts=3, payload_schema=SLEEP_SCHEMA, handler=_sleep),
        TaskKindSpec("system.sleep_gpu", "GPU-class sleep (serial resource demo)", ResourceClass.GPU,
                     payload_schema=SLEEP_SCHEMA, handler=_sleep),
        TaskKindSpec("system.guarded", "sleep that requires approval before queueing", ResourceClass.CPU,
                     approval_subject="task", payload_schema=SLEEP_SCHEMA, handler=_sleep),
        maintenance_kind(settings),
    ]
