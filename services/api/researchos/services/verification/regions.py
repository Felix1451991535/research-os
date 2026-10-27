"""Failure-region rules (shallow decision tree -> readable rules) and group-level bootstrap confidence intervals."""
from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any


def failure_rules(points: list[dict[str, float]], failed: list[bool], *, max_depth: int = 3, min_support: int = 4,
                  min_purity: float = 0.8) -> list[dict[str, Any]]:
    """Leaves of a depth<=3 tree that predict failure with purity >= min_purity and support >= min_support,
    expressed as conjunctions of thresholds, e.g. 'lr > 0.3 and batch <= 24 -> 6/6 failed'."""
    if not points or not any(failed) or all(failed):
        return []
    from sklearn.tree import DecisionTreeClassifier
    names = sorted(points[0])
    X = [[p[n] for n in names] for p in points]
    tree = DecisionTreeClassifier(max_depth=max_depth, min_samples_leaf=2, random_state=0).fit(X, failed)
    t = tree.tree_
    rules: list[dict[str, Any]] = []

    def walk(node: int, conds: list[str]) -> None:
        if t.children_left[node] == t.children_right[node]:  # leaf
            n = int(t.n_node_samples[node])
            leaf_idx = [i for i, x in enumerate(X) if tree.apply([x])[0] == node]
            nfail = sum(1 for i in leaf_idx if failed[i])
            if n >= min_support and nfail / n >= min_purity:
                rules.append({"rule": " and ".join(conds) or "always", "failed": nfail, "support": n,
                              "purity": round(nfail / n, 3)})
            return
        name, thr = names[t.feature[node]], float(t.threshold[node])
        walk(int(t.children_left[node]), [*conds, f"{name} <= {thr:.4g}"])
        walk(int(t.children_right[node]), [*conds, f"{name} > {thr:.4g}"])

    walk(0, [])
    return rules


def group_bootstrap_ci(rows: list[dict[str, str]], group_of: Callable[[str], str],
                       metric: Callable[[list[dict[str, str]]], float], *, b: int = 1000, seed: int = 0,
                       level: float = 0.95) -> dict[str, Any]:
    """Resample GROUPS (e.g. subjects), not windows: overlapping windows are not independent (baseline §9)."""
    by: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        by.setdefault(group_of(r["sample_id"]), []).append(r)
    keys = sorted(by)
    rng = random.Random(seed)
    stats = []
    for _ in range(b):
        sample = [r for k in (rng.choice(keys) for _ in keys) for r in by[k]]
        stats.append(metric(sample))
    stats.sort()
    lo, hi = stats[int((1 - level) / 2 * b)], stats[min(b - 1, int((1 + level) / 2 * b))]
    return {"estimate": metric(rows), "ci_low": lo, "ci_high": hi, "level": level, "groups": len(keys), "b": b}
