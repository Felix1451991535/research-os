"""K2 task kinds for literature monitoring. Handlers need the live Services (keystore with captured secrets,
extensions, blob store, frozen kind registry); wiring binds them after the container is built (`Late`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...contracts.errors import ApiError
from ...contracts.tasks import ResourceClass, TaskContext, TaskKindSpec, TaskOutcome, TaskState
from ...kernel.db import connect
from ..knowledge.writer import DbKnowledgeWriter
from . import monitor, organize, retraction
from .discovery import Ctx


@dataclass
class Late:
    svc: Any = None  # researchos.services.container.Services, set by wiring


def _saved_search(late: Late) -> TaskKindSpec:
    def run(ctx: TaskContext) -> TaskOutcome:
        svc = late.svc
        conn = connect(svc.settings.db_path)
        try:
            sid = str(ctx.payload["saved_search_id"])
            s = monitor.get(conn, sid)
            if not s["enabled"]:
                return TaskOutcome(TaskState.SUCCEEDED, {"skipped": "disabled"})
            actor = f"task:{ctx.task_id}"
            writer = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.literature", actor=actor,
                                       project_id=s["project_id"], max_bytes=svc.settings.max_upload_mb * 1024 * 1024)
            res = monitor.run(conn, Ctx(svc.extensions, svc.keystore, actor, s["project_id"]), writer, sid)
        finally:
            conn.close()
        return TaskOutcome(TaskState.SUCCEEDED, {"run_id": res["run_id"], "new": len(res["new"]),
                                                 "imported": len(res["imported"])})

    return TaskKindSpec("literature.saved_search", "run one saved search (new papers, auto-screen, opt-in import)",
                        ResourceClass.NETWORK, payload_schema={"type": "object", "required": ["saved_search_id"],
                                                               "properties": {"saved_search_id": {"type": "string"}},
                                                               "additionalProperties": False}, handler=run)


def _retractions(late: Late) -> TaskKindSpec:
    def run(ctx: TaskContext) -> TaskOutcome:
        svc = late.svc
        conn = connect(svc.settings.db_path)
        try:
            res = retraction.check_works(conn, Ctx(svc.extensions, svc.keystore, f"task:{ctx.task_id}"))
        finally:
            conn.close()
        return TaskOutcome(TaskState.SUCCEEDED, {"checked": res["checked"], "retracted": len(res["retracted"]),
                                                 "errors": res["errors"]})

    return TaskKindSpec("literature.retraction_check", "check the library for retractions (weekly)",
                        ResourceClass.NETWORK, payload_schema={"type": "object", "additionalProperties": False},
                        handler=run)


ORGANIZE_SCHEMA = {"type": "object", "required": ["source_dir", "library_dir"], "additionalProperties": False,
                   "properties": {"source_dir": {"type": "string"}, "library_dir": {"type": "string"},
                                  "mode": {"enum": ["copy", "move"]}, "project_id": {"type": ["string", "null"]}}}


def _organize(late: Late) -> TaskKindSpec:
    def run(ctx: TaskContext) -> TaskOutcome:
        svc, p = late.svc, ctx.payload
        conn = connect(svc.settings.db_path)
        try:
            actor = f"task:{ctx.task_id}"
            writer = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.library", actor=actor,
                                       project_id=p.get("project_id"),
                                       max_bytes=svc.settings.max_upload_mb * 1024 * 1024)
            res = organize.propose(conn, Ctx(svc.extensions, svc.keystore, actor, p.get("project_id")), writer,
                                   svc.settings.data_dir / "home", source_dir=p["source_dir"],
                                   library_dir=p["library_dir"], mode=p.get("mode", "copy"))
        except ApiError as e:
            return TaskOutcome(TaskState.FAILED, error_code=e.code, error_message=e.message)
        finally:
            conn.close()
        return TaskOutcome(TaskState.SUCCEEDED, {"plan_id": res["plan"]["id"] if res["plan"] else None,
                                                 "notes": res["notes"][-20:]})

    return TaskKindSpec("library.organize", "scan a folder, identify PDFs and propose a library EffectPlan",
                        ResourceClass.NETWORK, payload_schema=ORGANIZE_SCHEMA, handler=run)


def literature_kinds(late: Late) -> list[TaskKindSpec]:
    return [_saved_search(late), _retractions(late), _organize(late)]
