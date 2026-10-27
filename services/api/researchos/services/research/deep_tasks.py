"""K2 task + trigger for the deep research pipeline. The trigger enqueues at most one advance per run per tick
bucket, so approvals and screening decisions are picked up without polling from the browser."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from ...contracts.tasks import ResourceClass, TaskContext, TaskKindSpec, TaskOutcome, TaskState
from ...kernel.db import connect, rows
from ..orchestration import tasks as task_svc
from ..orchestration.registry import KindRegistry
from . import deep

ACTIVE = ("planning", "awaiting_plan_approval", "searching", "awaiting_screening", "awaiting_import_approval",
          "reading", "synthesizing")


def enqueue(conn: sqlite3.Connection, kinds: KindRegistry, rid: str, key: str, actor: str) -> dict[str, Any]:
    t, _ = task_svc.create(conn, kinds, kind="research.deep_advance", payload={"research_id": rid}, actor=actor,
                           idempotency_key=f"dr:{rid}:{key}")
    return t


def trigger(conn: sqlite3.Connection, kinds: KindRegistry, now: datetime) -> int:
    bucket = now.strftime("%Y%m%d%H%M")
    n = 0
    for r in rows(conn.execute(f"SELECT id, stage FROM deep_research WHERE stage IN ({','.join('?' * len(ACTIVE))})",
                               ACTIVE)):
        before = conn.total_changes
        enqueue(conn, kinds, r["id"], f"{r['stage']}:{bucket}", "system:scheduler")
        n += int(conn.total_changes > before)
    return n


def deep_kind(late: Any) -> TaskKindSpec:
    def run(ctx: TaskContext) -> TaskOutcome:
        conn = connect(late.svc.settings.db_path)
        try:
            stage = deep.advance(conn, late.svc, str(ctx.payload["research_id"]), f"task:{ctx.task_id}")
        finally:
            conn.close()
        return TaskOutcome(TaskState.SUCCEEDED, {"stage": stage})

    return TaskKindSpec("research.deep_advance", "advance a deep research run by one stage", ResourceClass.NETWORK,
                        payload_schema={"type": "object", "required": ["research_id"], "additionalProperties": False,
                                        "properties": {"research_id": {"type": "string"}}}, handler=run)
