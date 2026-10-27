"""Mark a template as reviewed: records its current content hash in REVIEWED.json next to it.
Run ONLY after reading the template code (baseline §7: v1 runs reviewed templates only).
Usage: python scripts/review_template.py templates <template_id>
       python scripts/review_template.py <dir containing the template> <template_id> --into <data>/config/REVIEWED.json
       (plugin templates: only the owner file <data>/config/REVIEWED.json counts)"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))
from researchos.services.execution.templates import REVIEWED_FILE, template_hash  # noqa: E402


def main() -> int:
    root, tid = Path(sys.argv[1]), sys.argv[2]
    f = Path(sys.argv[sys.argv.index("--into") + 1]) if "--into" in sys.argv else root / REVIEWED_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    reviewed = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    reviewed[tid] = template_hash(root / tid)
    f.write_text(json.dumps(reviewed, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{tid}: {reviewed[tid]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
