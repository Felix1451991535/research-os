"""Run report export = snapshot_from_run + the core Markdown exporter (single rendering path)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .export_host import publish, snapshot_from_run
from .md_exporter import MarkdownExporter


def export_run(conn: sqlite3.Connection, data_dir: Path, run_id: str) -> dict[str, Any]:
    res = publish(conn, data_dir, MarkdownExporter(), snapshot_from_run(conn, data_dir, run_id), "final", {}, "system")
    md = next(f for f in res["files"] if f["mime"] == "text/markdown")
    html = next(f for f in res["files"] if f["mime"] == "text/html")
    missing = [d["ref"] for d in res["diagnostics"] if d["code"] == "unresolved_metric"]
    return {
        "markdown": str(data_dir / md["path"]),
        "html": str(data_dir / html["path"]),
        "missing_numbers": missing,
        "export": res,
    }
