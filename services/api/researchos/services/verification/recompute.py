"""Independent metric recomputation from saved predictions (dual implementation: never the template's code)."""

from __future__ import annotations

from collections.abc import Callable


def accuracy(rows: list[dict[str, str]]) -> float:
    return sum(r["y_true"] == r["y_pred"] for r in rows) / len(rows) if rows else float("nan")


def macro_f1(rows: list[dict[str, str]]) -> float:
    labels = sorted({r["y_true"] for r in rows} | {r["y_pred"] for r in rows})
    if not labels:
        return float("nan")
    f1s = []
    for c in labels:
        tp = sum(r["y_true"] == c and r["y_pred"] == c for r in rows)
        fp = sum(r["y_true"] != c and r["y_pred"] == c for r in rows)
        fn = sum(r["y_true"] == c and r["y_pred"] != c for r in rows)
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(f1s) / len(f1s)


RECOMPUTERS: dict[str, Callable[[list[dict[str, str]]], float]] = {"accuracy": accuracy, "macro_f1": macro_f1}
TOLERANCE = 1e-6


def base_name(metric: str) -> str:
    """'rf.macro_f1' -> 'macro_f1' (prefix = model / predictions key)."""
    return metric.rsplit(".", 1)[-1]
