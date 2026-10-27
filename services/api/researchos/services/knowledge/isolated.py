"""Run the parser in a child process with a hard timeout (kill tree) and rebuild the typed result."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ...contracts.knowledge import PageQuality, ParsedChunk, ParsedPage, ParseError, ParseResult
from ...platform.env import child_env
from ...platform.process import kill_tree, spawn


def _from_dict(d: dict[str, Any]) -> ParseResult:
    pages = tuple(ParsedPage(p["page_index"], p["text"], p["width"], p["height"], p["rotation"],
                             PageQuality(p["quality"]), tuple(p["flags"]), p["page_label"],
                             tuple(ParsedChunk(c["char_start"], c["char_end"], c["text"], c["section_role"],
                                               c["chunk_kind"], tuple(c["bbox"]) if c["bbox"] else None)
                                   for c in p["chunks"])) for p in d["pages"])
    return ParseResult(d["parser_version"], pages, d["metadata"], tuple(d["warnings"]))


def parse_isolated(src: Path, *, max_pages: int, timeout_s: float, home: Path) -> ParseResult:
    with tempfile.TemporaryDirectory(dir=home.parent if home.parent.exists() else None) as td:
        out = Path(td) / "out.json"
        child = spawn([sys.executable, "-m", "researchos.services.knowledge.parse_child", str(src), str(out),
                       str(max_pages)], cwd=Path(td), env=child_env(home), stdout=subprocess.DEVNULL,
                      stderr=subprocess.PIPE)
        end = time.monotonic() + timeout_s
        while child.popen.poll() is None:
            if time.monotonic() > end:
                kill_tree(child.pid, child.create_time, child.job, grace_s=0.2)
                raise ParseError("timeout", f"parsing exceeded {timeout_s:.0f} s and was stopped",
                                 "the file may be malformed; try re-exporting the PDF")
            time.sleep(0.05)
        if not out.is_file():
            err = child.popen.stderr.read().decode("utf-8", "replace")[-300:] if child.popen.stderr else ""
            raise ParseError("crashed", f"parser process failed: {err}", "re-export the PDF and import again")
        data = json.loads(out.read_text(encoding="utf-8"))
    if not data["ok"]:
        raise ParseError(data["code"], data["message"], data["recovery"])
    return _from_dict(data["result"])
