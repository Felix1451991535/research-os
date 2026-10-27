"""Task kind `knowledge.parse_document`: worker-side parse of one document version (idempotent)."""
from __future__ import annotations

from ...contracts.knowledge import ParseError
from ...contracts.tasks import ResourceClass, RetryPolicy, TaskContext, TaskKindSpec, TaskOutcome, TaskState
from ...kernel.config import Settings
from ...kernel.db import connect, row, tx
from . import reading
from .isolated import parse_isolated
from .pdf import PdfParser
from .store_parse import record_failure, store_result

SCHEMA = {"type": "object", "properties": {"version_id": {"type": "string"}}, "required": ["version_id"],
          "additionalProperties": False}


def parse_kind(settings: Settings) -> TaskKindSpec:
    parser = PdfParser()

    def run(ctx: TaskContext) -> TaskOutcome:
        vid = str(ctx.payload["version_id"])
        conn = connect(settings.db_path)
        try:
            v = row(conn.execute("SELECT * FROM document_versions WHERE version_id=?", (vid,)))
            if v is None:
                return TaskOutcome(TaskState.FAILED, error_code="not_found", error_message=vid)
            if v["parser_version"] == parser.version and v["parse_state"] in ("parsed", "partial", "needs_ocr"):
                return TaskOutcome(TaskState.SUCCEEDED, {"state": v["parse_state"], "reused": True})
            with tx(conn):
                conn.execute("UPDATE document_versions SET parse_state='parsing' WHERE version_id=?", (vid,))
            ctx.progress("parsing", {"file": v["original_name"]})
            try:
                res = parse_isolated(settings.data_dir / v["stored_path"], max_pages=settings.max_pdf_pages,
                                     timeout_s=settings.parse_timeout_s, home=settings.data_dir / "home")
            except ParseError as e:
                record_failure(conn, vid, e, f"task:{ctx.task_id}")
                return TaskOutcome(TaskState.SUCCEEDED, {"state": "failed", "code": e.code, "recovery": e.recovery})
            if not ctx.check_lease():
                return TaskOutcome(TaskState.FAILED, error_code="lease_lost")
            state = store_result(conn, vid, res, f"task:{ctx.task_id}")
            aids = reading.extract(conn, vid) if state in ("parsed", "partial") else {}  # captions + main points
            return TaskOutcome(TaskState.SUCCEEDED, {"state": state, "pages": len(res.pages), "reading": aids})
        finally:
            conn.close()

    return TaskKindSpec("knowledge.parse_document", "parse an imported document into pages + chunks",
                        ResourceClass.CPU, RetryPolicy.IDEMPOTENT, max_attempts=2, payload_schema=SCHEMA, handler=run)
