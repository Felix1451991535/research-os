"""Host-drawn run figures (order f / V1 item 7, ADR-194): for classification runs, a confusion matrix and the
per-subject balanced accuracy with the chance level, drawn by the HOST from the run's saved predictions and the
host-materialised sample -> subject mapping - not by the template, so every classification template gets them and
reviewed templates stay unchanged. Predictions are template output (untrusted): rows and classes are capped and
unparseable files are skipped. Plain, printable (grey + one accent), >= 12 pt, deterministic PNG. matplotlib is
optional (experiments extra); without it no figures are drawn and the result says so."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

OUT = "host_figures"
MAX_ROWS = 1_000_000
MAX_CLASSES = 50
ACCENT = "#1f5fa8"


def _plt() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 12, "figure.dpi": 150})
    return plt


def _rows(path: Path) -> list[tuple[str, str, str]] | None:
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            out = []
            for i, r in enumerate(csv.DictReader(fh)):
                if i >= MAX_ROWS:
                    return None
                out.append((str(r["sample_id"]), str(r["y_true"]), str(r["y_pred"])))
            return out
    except (OSError, KeyError, UnicodeDecodeError, csv.Error):
        return None


def _groups(inputs: Path, split: str) -> dict[str, str]:
    import numpy as np

    f = inputs / f"{split}.npz"
    if not f.is_file():
        return {}
    with np.load(f, allow_pickle=False) as npz:
        if "group" not in npz.files:
            return {}
        return {str(s): str(g) for s, g in zip(npz["sample_id"], npz["group"], strict=True)}


def _save(fig: Any, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png", metadata={"Software": None}, bbox_inches="tight")
    return path.name


def confusion(rows: list[tuple[str, str, str]], split: str, out: Path) -> str:
    plt = _plt()
    classes = sorted({t for _, t, _ in rows} | {p for _, _, p in rows})
    idx = {c: i for i, c in enumerate(classes)}
    cm = [[0] * len(classes) for _ in classes]
    for _, t, p in rows:
        cm[idx[t]][idx[p]] += 1
    top = max(max(r) for r in cm)
    fig, ax = plt.subplots(figsize=(1.0 + 0.9 * len(classes), 0.8 + 0.8 * len(classes)))
    ax.imshow(cm, cmap="Greys", vmin=0, vmax=max(1, top))
    for i, r in enumerate(cm):
        for j, v in enumerate(r):
            ax.text(j, i, str(v), ha="center", va="center", color="white" if v > top / 2 else "black")
    ax.set_xticks(range(len(classes)), classes)
    ax.set_yticks(range(len(classes)), classes)
    ax.set_xlabel("predicted")
    ax.set_ylabel("true")
    ax.set_title(f"Confusion matrix ({split}, n={len(rows)})")
    name = _save(fig, out / f"confusion_{split}.png")
    plt.close(fig)
    return name


def per_subject(rows: list[tuple[str, str, str]], groups: dict[str, str], split: str, out: Path) -> str | None:
    by: dict[str, list[tuple[str, str]]] = {}
    for s, t, p in rows:
        if s in groups:
            by.setdefault(groups[s], []).append((t, p))
    if not by:
        return None
    names = sorted(by)
    acc = []
    for g in names:
        pairs = by[g]
        present = {t for t, _ in pairs}
        recalls = [sum(p == c for t, p in pairs if t == c) / sum(t == c for t, _ in pairs) for c in present]
        acc.append(sum(recalls) / len(recalls))
    n_classes = len({t for _, t, _ in rows})
    plt = _plt()
    fig, ax = plt.subplots(figsize=(max(4.0, 0.55 * len(names) + 2), 3.6))
    ax.bar(range(len(names)), acc, color=ACCENT)
    chance = 1 / max(1, n_classes)
    ax.axhline(chance, color="black", linestyle="--", linewidth=1)
    ax.text(len(names) - 0.5, chance + 0.01, f"chance {chance:.2f}", ha="right", va="bottom")
    ax.set_xticks(range(len(names)), names, rotation=45 if len(names) > 6 else 0)
    ax.set_ylim(0, 1)
    ax.set_ylabel("balanced accuracy")
    ax.set_title(f"Per subject ({split})")
    name = _save(fig, out / f"per_subject_{split}.png")
    plt.close(fig)
    return name


def draw(run_dir: Path, task: dict[str, Any], allowed: list[str]) -> dict[str, Any]:
    """Figures for each non-train prediction file of a classification run. Never raises."""
    if task.get("type") != "classification":
        return {"figures": [], "note": "not a classification template"}
    try:
        _plt()
    except ImportError:
        return {"figures": [], "note": "matplotlib not installed (experiments extra): no figures drawn"}
    try:
        return _draw_all(run_dir, allowed)
    except Exception as e:  # noqa: BLE001 - figures are a by-product; a drawing failure never fails the run
        return {"figures": [], "note": f"figure drawing failed: {type(e).__name__}"}


def _draw_all(run_dir: Path, allowed: list[str]) -> dict[str, Any]:
    made: list[str] = []
    skipped: list[dict[str, str]] = []
    for split in (s for s in allowed if s != "train"):
        rows = _rows(run_dir / "predictions" / f"{split}.csv")
        if not rows:
            continue
        if len({t for _, t, _ in rows} | {p for _, _, p in rows}) > MAX_CLASSES:
            skipped.append({"split": split, "reason": f"more than {MAX_CLASSES} classes"})
            continue
        made.append(confusion(rows, split, run_dir / OUT))
        sub = per_subject(rows, _groups(run_dir / "inputs", split), split, run_dir / OUT)
        if sub:
            made.append(sub)
    return {"figures": made, "skipped": skipped}


def figure_file(conn: Any, data_dir: Path, run_id: str, name: str) -> Path:
    """A registered `figure` artifact of this run, by file name (ADR-209): confined to the run directory, no links,
    bytes still equal to the recorded hash. Anything else is 404 without saying why."""
    import hashlib

    from ...contracts.errors import not_found
    from ...platform.paths import has_reparse_point

    for a in conn.execute("SELECT path, sha256 FROM artifacts WHERE run_id=? AND kind='figure'", (run_id,)):
        if Path(a[0]).name != name:
            continue
        candidate, root = data_dir / str(a[0]), (data_dir / "runs" / run_id).resolve()
        if has_reparse_point(candidate, data_dir) or not candidate.is_file():
            break
        resolved = candidate.resolve()
        if resolved.is_relative_to(root) and hashlib.sha256(resolved.read_bytes()).hexdigest() == a[1]:
            return resolved
        break
    raise not_found("figure")
