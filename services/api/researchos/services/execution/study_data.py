"""Study persistence, child-run enqueueing + result collection (rows of params/seed/objective/failed)."""

from __future__ import annotations

import hashlib
import math
import sqlite3
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...contracts.tasks import TERMINAL
from ...kernel.db import Row, jdump, jload, row, rows, tx, utcnow
from ..orchestration import tasks as task_svc
from ..orchestration.registry import KindRegistry
from . import sampling


def get(conn: sqlite3.Connection, sid: str) -> Row:
    s = row(conn.execute("SELECT * FROM studies WHERE id=?", (sid,)))
    if s is None:
        raise not_found("study")
    s["protocol"], s["data"] = jload(s["protocol"]), jload(s["data"]) or {}
    return s


def save(conn: sqlite3.Connection, sid: str, **fields: Any) -> None:
    sets = {k: jdump(v) if isinstance(v, dict) else v for k, v in fields.items()}
    with tx(conn):
        conn.execute(
            f"UPDATE studies SET {', '.join(f'{k}=?' for k in sets)}, updated_at=? WHERE id=?",
            (*sets.values(), utcnow(), sid),
        )


def protocol_hash(protocol: dict[str, Any]) -> str:
    return hashlib.sha256(jdump(protocol).encode()).hexdigest()


def children(conn: sqlite3.Connection, sid: str, stage_tag: str) -> list[Row]:
    return rows(
        conn.execute(
            "SELECT t.*, (SELECT id FROM runs r WHERE r.task_id=t.id ORDER BY attempt DESC LIMIT 1) AS run_id "
            "FROM tasks t "
            "WHERE t.kind='experiment.run' AND json_extract(t.payload,'$.study_id')=? "
            "AND json_extract(t.payload,'$.tag')=? ORDER BY t.created_at",
            (sid, stage_tag),
        )
    )


def all_done(ch: list[Row]) -> bool:
    return bool(ch) and all(c["state"] in {s.value for s in TERMINAL} | {"blocked"} for c in ch)


def results(conn: sqlite3.Connection, ch: list[Row], objective: str) -> list[dict[str, Any]]:
    out = []
    for c in ch:
        p = jload(c["payload"])
        m = (
            row(
                conn.execute(
                    "SELECT value, verification FROM run_metrics WHERE run_id=? AND name=?", (c["run_id"], objective)
                )
            )
            if c["run_id"]
            else None
        )
        ok = (
            c["state"] == "succeeded"
            and m is not None
            and m["verification"] != "failed"
            and m["value"] is not None
            and math.isfinite(m["value"])
        )
        out.append(
            {
                **p["params"],
                "seed": p["seed"],
                "is_center": bool(p.get("center")),
                "point": p.get("point"),
                "objective": float(m["value"]) if ok and m else None,
                "failed": not ok,
                "run_id": c["run_id"],
                "state": c["state"],
            }
        )
    return out


def config_key(params: dict[str, Any], space_keys: list[str]) -> str:
    return jdump({k: params[k] for k in sorted(space_keys) if k in params})


def enqueue(
    conn: sqlite3.Connection,
    kinds: KindRegistry,
    st: dict[str, Any],
    tag: str,
    stage: str,
    points: list[dict[str, Any]],
    seeds: list[int],
    actor: str,
    control: str | None = None,
) -> int:
    """Child experiment runs of a study (one per point x seed), idempotent per study/tag/point/seed."""
    constraints = st["protocol"].get("constraints", [])
    bad = [p for p in points if not sampling.feasible(p, [], constraints)]
    if bad:  # every enqueued configuration satisfies the constraints, whatever produced it (review #57)
        raise ApiError(422, "constraint_violation", f"{len(bad)} {tag} configuration(s) violate {constraints}")
    n = 0
    for i, pt in enumerate(points):
        params = {k: v for k, v in pt.items() if not k.startswith("_")}
        for seed in seeds:
            payload: dict[str, Any] = {
                "plan_id": st["plan_id"],
                "revision": st["current_revision"],
                "stage": stage,
                "params": params,
                "seed": seed,
                "study_id": st["id"],
                "tag": tag,
                "point": i,
                "center": bool(pt.get("_center")),
            }
            if control:
                payload["control"] = control
            task_svc.create(
                conn,
                kinds,
                kind="experiment.run",
                payload=payload,
                actor=actor,
                idempotency_key=f"study:{st['id']}:{tag}:{i}:{seed}",
            )
            n += 1
    return n
