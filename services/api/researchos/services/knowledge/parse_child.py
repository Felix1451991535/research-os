"""Isolated parser process: `python -m researchos.services.knowledge.parse_child <in.pdf> <out.json> <max_pages>`.
Runs the pure parser in a separate process so a pathological PDF can be killed by a hard timeout."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from ...contracts.knowledge import ParseError
from .pdf import PdfParser


def main() -> int:
    src, out, max_pages = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    try:
        res = PdfParser().parse(src.read_bytes(), max_pages=max_pages)
        out.write_text(json.dumps({"ok": True, "result": asdict(res)}), encoding="utf-8")
    except ParseError as e:
        out.write_text(json.dumps({"ok": False, "code": e.code, "message": str(e), "recovery": e.recovery}),
                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
