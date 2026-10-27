"""Read-only RunView over a run directory (the detectors' only input)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any


def read_jsonl(p: Path, start: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Parse complete lines from byte offset `start`; returns (records, new offset). Tolerates a partial last line."""
    if not p.is_file():
        return [], start
    with p.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    out: list[dict[str, Any]] = []
    consumed = 0
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        consumed += len(line)
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"kind": "corrupt_line"})
    return out, start + consumed


class DirRunView:
    def __init__(
        self,
        run_dir: Path,
        template: dict[str, Any],
        split_ids: dict[str, set[str]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.run_dir = run_dir
        self._template = template
        self._split_ids = split_ids or {}
        self.extra = extra or {}  # monitor facts: exit_code, suspend gaps, idle, materialization info

    def metric_records(self) -> list[dict[str, Any]]:
        return read_jsonl(self.run_dir / "metrics.jsonl")[0]

    def summary(self) -> list[dict[str, Any]]:
        p = self.run_dir / "summary.json"
        if not p.is_file():
            return []
        data: list[dict[str, Any]] = json.loads(p.read_text(encoding="utf-8"))
        return data

    def predictions(self, key: str) -> list[dict[str, str]] | None:
        p = self.run_dir / "predictions" / f"{key}.csv"
        if not p.is_file():
            return None
        with p.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def prediction_keys(self) -> list[str]:
        d = self.run_dir / "predictions"
        return sorted(p.stem for p in d.glob("*.csv")) if d.is_dir() else []

    def split_ids(self, split: str) -> set[str] | None:
        return self._split_ids.get(split)

    def accessed_splits(self) -> list[str]:
        recs, _ = read_jsonl(self.run_dir / "access.log")
        return [str(r.get("split")) for r in recs]

    @property
    def manifest(self) -> dict[str, Any]:
        p = self.run_dir / "run.json"
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        return data

    @property
    def template(self) -> dict[str, Any]:
        return self._template


def finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False
