"""Egress gate (ADR-159): the single check that keeps content derived from `local_only` datasets away from cloud model
providers. Gateway.call runs it before any provider attempt, from two independent sources:

1. declared references - the caller's (kind, id) pairs: ("dataset", id), ("plan", "plan_id@revision"), ("run", id);
2. detection - every rendered message is scanned for fingerprints of restricted datasets (dataset id, manifest hash,
   sample ids, group ids), so a caller that forgets to declare is still caught.

A restricted dataset is one whose governance egress is not `cloud_ok` (unknown -> `local_only`; `cloud_redacted`
counts as restricted until a redaction step exists). The result names datasets, never content.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ...contracts.datasources import EGRESS_POLICIES
from ...kernel.db import jload, row, rows
from .egress_index import MIN_FINGERPRINT, reindex_missing

# Identifier-ish runs (any script); separators split them into parts so "sample:s03-c2-w12", "/samples/s03-c2-w12"
# and "s03-c2-w12.csv" all expose "s03-c2-w12" (review #49). Matching is case-insensitive.
TOKEN = re.compile(r"[\w][\w.:@/\\-]*")
SEPARATORS = re.compile(r"([.:@/\\])")
KINDS = ("dataset", "plan", "run", "artifact", "answer", "tool_output")
DataRef = tuple[str, str]


@dataclass(frozen=True)
class Decision:
    local_only: bool
    datasets: tuple[str, ...]  # restricted datasets that triggered it
    how: str  # "declared" | "detected" | "declared+detected" | ""


def policy_of(governance: str | None) -> str:
    g = jload(governance or "{}") or {}
    p = g.get("egress") if isinstance(g, dict) else None
    return p if p in EGRESS_POLICIES else "local_only"


def restricted(policy: str) -> bool:
    return policy != "cloud_ok"


def _plan_dataset(conn: sqlite3.Connection, ref: str) -> set[str]:
    pid, _, rev = ref.partition("@")
    p = row(conn.execute("SELECT content FROM plan_revisions WHERE plan_id=? AND revision=?", (pid, rev or 1)))
    if p is None:
        raise ValueError(f"unknown plan {ref!r}")
    dataset = (jload(p["content"]) or {}).get("dataset")
    return {str(dataset["id"])} if isinstance(dataset, dict) and dataset.get("id") else set()


def _run_dataset(conn: sqlite3.Connection, run_id: str) -> set[str]:
    t = row(conn.execute("SELECT t.payload FROM runs r JOIN tasks t ON t.id=r.task_id WHERE r.id=?", (run_id,)))
    if t is None:
        raise ValueError(f"unknown run {run_id!r}")
    payload = jload(t["payload"]) or {}
    return _plan_dataset(conn, f"{payload.get('plan_id')}@{payload.get('revision')}")


def _one(conn: sqlite3.Connection, kind: str, ref: str) -> set[str]:
    """Datasets behind one reference; ValueError for an unknown kind or an unresolvable id (fail closed, #52)."""
    if kind == "dataset":
        return {ref}
    if kind == "plan":
        return _plan_dataset(conn, ref)
    if kind == "run":
        return _run_dataset(conn, ref)
    if kind == "artifact":
        a = row(conn.execute("SELECT run_id FROM artifacts WHERE id=?", (ref,)))
        if a is None:
            raise ValueError(f"unknown artifact {ref!r}")
        return _run_dataset(conn, a["run_id"])
    if kind == "answer":  # answers record the restricted datasets their model calls carried (derived content, #47)
        a = row(conn.execute("SELECT body FROM answers WHERE id=?", (ref,)))
        if a is None:
            raise ValueError(f"unknown answer {ref!r}")
        body = jload(a["body"]) or {}
        if "egress_restricted" not in body:  # older answers: absent provenance is not "verified empty" (#55)
            raise ValueError(f"answer {ref!r} has no recorded provenance")
        return {str(d) for d in body["egress_restricted"]}
    if kind == "tool_output":  # a tool whose output may carry stored data and declares no provenance (#47)
        raise ValueError(f"output of tool {ref!r} has no declared provenance")
    raise ValueError(f"unsupported data reference kind {kind!r} (supported: {', '.join(KINDS)})")


def _declared(conn: sqlite3.Connection, refs: Sequence[DataRef]) -> set[str]:
    ids: set[str] = set()
    for kind, ref in refs:
        ids |= _one(conn, kind, ref)
    return ids


def candidates(text: str) -> set[str]:
    """Every contiguous run of separator-delimited parts of every identifier-ish token, lower-cased."""
    out: set[str] = set()
    for m in TOKEN.finditer(text.lower()):
        pieces = SEPARATORS.split(m.group(0))  # [part, sep, part, sep, ...]
        parts = pieces[0::2]
        for a in range(len(parts)):
            for b in range(a, min(len(parts), a + 8)):
                cand = "".join(pieces[2 * a : 2 * b + 1]).strip("-")
                if len(cand) >= MIN_FINGERPRINT:
                    out.add(cand)
    return out


def _detected(conn: sqlite3.Connection, texts: Iterable[str], declared: set[str]) -> set[str]:
    """Datasets whose fingerprints appear in the text. An `identity` match (dataset id, manifest hash) always counts;
    a `content` token (sample / group id) that also belongs to a declared dataset is attributed to it (#51, #54)."""
    batch = sorted(set().union(*(candidates(t) for t in texts)) if texts else set())
    found: set[str] = set()
    owners: dict[str, set[str]] = {}
    for i in range(0, len(batch), 500):
        chunk = batch[i : i + 500]
        q = (f"SELECT token, dataset_id, kind FROM dataset_fingerprints WHERE kind != 'marker' AND token IN "
             f"({','.join('?' * len(chunk))})")
        for r in rows(conn.execute(q, chunk)):
            if r["kind"] == "identity":
                found.add(r["dataset_id"])
            else:
                owners.setdefault(r["token"], set()).add(r["dataset_id"])
    return found | {d for ds in owners.values() if not ds & declared for d in ds}


def check(conn: sqlite3.Connection, refs: Sequence[DataRef], texts: Iterable[str]) -> Decision:
    try:
        declared = _declared(conn, refs)
    except ValueError as e:
        return Decision(True, tuple(sorted(f"{k}:{r}" for k, r in refs)), f"declared ({e})")
    texts = list(texts)
    incomplete = {ds["id"] for ds in reindex_missing(conn) if restricted(policy_of(ds["governance"]))}
    detected = _detected(conn, texts, declared)
    involved = declared | detected | incomplete  # an unindexed restricted dataset may be in any prompt (#53)
    if not involved:
        return Decision(False, (), "")
    marks = ",".join("?" * len(involved))
    known = {r["id"]: r["governance"] for r in rows(conn.execute(
        f"SELECT id, governance FROM datasets WHERE id IN ({marks})", sorted(involved)))}
    bad = sorted(d for d in involved if restricted(policy_of(known.get(d))))  # unknown dataset -> local_only
    if not bad:
        return Decision(False, (), "")
    how = "+".join(x for x, s in (("declared", declared), ("detected", detected), ("index incomplete", incomplete))
                   if set(bad) & s)
    return Decision(True, tuple(bad), how)


def describe(d: Decision) -> str:
    return f"egress: content from local_only dataset(s) {list(d.datasets)} ({d.how}) may only go to a local model"


def refs_of(datasets: Iterable[str]) -> tuple[DataRef, ...]:
    """Carry a restriction into a dependent call (writer -> verifier, agent step -> next step; #47)."""
    return tuple(("dataset", d) for d in sorted(set(datasets)))
