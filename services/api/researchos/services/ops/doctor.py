"""`researchos-api doctor`: run all checks (doctor_checks.py) and print PASS/WARN/FAIL/NOT RUN with hints."""
from __future__ import annotations

import json

from ...kernel.config import Settings
from .doctor_checks import ALL_CHECKS, Report


def run_checks(settings: Settings) -> list[dict[str, str]]:
    r = Report()
    for check in ALL_CHECKS:
        check(r, settings)
    return r.items


def run_doctor(settings: Settings, as_json: bool = False) -> int:
    res = run_checks(settings)
    if as_json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
    else:
        for r in res:
            line = f"[{r['status']:7}] {r['check']}: {r['detail']}"
            if r["hint"]:
                line += f"\n          -> {r['hint']}"
            print(line)
    return 1 if any(r["status"] == "FAIL" for r in res) else 0
