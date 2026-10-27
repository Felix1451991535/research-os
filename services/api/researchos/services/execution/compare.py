"""Reproduce-and-compare (research-method feature 5, ADR-133): a completed K5 rerun against numbers quoted in a
paper. Paper numbers come only from re-verified Evidence quotes (selected by index), reproduced numbers only from
independently recomputed run metrics; no implicit unit conversion; deviations list evidence-sourced parameter
differences as *candidate causes*. Lives in execution (needs plans); verification cannot import execution."""

from __future__ import annotations

import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any

import jsonschema

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jload, row, tx
from ..knowledge import evidence
from ..knowledge.text import NUMBER_RE, canon_number, find_span
from ..verification.view import finite
from . import plans

# Request vocabulary is local to the existing service, not a new plugin/core contract.
_SOURCE: dict[str, Any] = {
    "type": "object",
    "required": ["evidence_id", "number_index"],
    "properties": {
        "evidence_id": {"type": "string", "minLength": 1},
        "number_index": {"type": "integer", "minimum": 0},
        "unit": {"type": ["string", "null"]},
    },
}
COMPARE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "maxItems": 200,
    "items": {
        **_SOURCE,
        "additionalProperties": False,
        "required": [*_SOURCE["required"], "metric", "split", "tolerance"],
        "properties": {
            **_SOURCE["properties"],
            "metric": {"type": "string", "minLength": 1},
            "split": {"type": "string", "minLength": 1},
            "tolerance": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "value"],
                "properties": {"kind": {"enum": ["absolute", "relative"]}, "value": {"type": "number", "minimum": 0}},
            },
        },
    },
}
PARAM_SCHEMA: dict[str, Any] = {
    "type": "array",
    "maxItems": 200,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "evidence_id"],
        "properties": {
            **_SOURCE["properties"],
            "name": {"type": "string", "minLength": 1},
            "text": {"type": "string", "minLength": 1},
        },
        "oneOf": [{"required": ["number_index"]}, {"required": ["text"]}],
    },
}


def _source(conn: sqlite3.Connection, spec: dict[str, Any]) -> dict[str, Any]:
    v = evidence.verify(conn, spec["evidence_id"])
    e = v.evidence
    if v.status != "ok" or e is None:
        raise ValueError(f"evidence {v.status}")
    if e["trust"] not in ("machine_checked", "human_checked") or e["kind"] not in ("source_fact", "author_conclusion"):
        raise ValueError("evidence is not a verified paper statement")
    return {k: e[k] for k in ("version_id", "page_index", "char_start", "char_end", "quote", "trust")}


def _quantity(value: str, unit: str | None) -> tuple[Decimal, str]:
    token = canon_number(value)
    if token.endswith("%"):
        if unit not in (None, "", "%", "1"):
            raise ValueError("percent conflicts with stated unit")
        token, unit = token[:-1], "%"
    number = Decimal(token)
    if not number.is_finite() or not finite(number):
        raise ValueError("nonfinite value")
    return (number / 100, "1") if unit == "%" else (number, unit or "1")


def _paper_number(source: dict[str, Any], spec: dict[str, Any]) -> tuple[Decimal, str]:
    tokens = list(NUMBER_RE.finditer(source["quote"]))
    if spec["number_index"] >= len(tokens):
        raise ValueError("selected number is absent from quote")
    return _quantity(tokens[spec["number_index"]].group(0), spec.get("unit"))  # scientific notation = one token


def _parameter_diffs(
    conn: sqlite3.Connection, specs: list[dict[str, Any]], version: str, plan: dict[str, Any], actual: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    diffs, notes = [], []
    for spec in specs:
        name = spec["name"]
        try:
            source = _source(conn, spec)
            if source["version_id"] != version:
                raise ValueError("parameter evidence belongs to another paper version")
            if "number_index" in spec:
                value, unit = _paper_number(source, spec)
                paper_value: Any = float(value)
                # Parameter units are explicitly supplied; plan/task numeric values use that unit too.
                pv = _quantity(str(plan[name]), spec.get("unit")) if name in plan else None
                rv = _quantity(str(actual[name]), spec.get("unit")) if name in actual else None
                different = pv != (value, unit) or rv != (value, unit)
            else:
                paper_value = spec["text"]
                if find_span(source["quote"], paper_value) is None:
                    raise ValueError("parameter text is absent from quote")
                different = plan.get(name) != paper_value or actual.get(name) != paper_value
            if different:
                diffs.append(
                    {
                        "label": "candidate cause",
                        "name": name,
                        "paper_value": paper_value,
                        "plan_value": plan.get(name),
                        "run_value": actual.get(name),
                        "evidence_id": spec["evidence_id"],
                        **source,
                    }
                )
        except (ValueError, InvalidOperation) as exc:
            notes.append(f"{name}: {exc}")
    return diffs, notes


def _compare_item(
    conn: sqlite3.Connection,
    run: dict[str, Any],
    spec: dict[str, Any],
    params: list[dict[str, Any]],
    plan: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        **spec,
        "status": "not comparable",
        "reported_value": None,
        "reproduced_value": None,
        "candidate_causes": [],
        "evidence_ref": "{{evidence:" + spec["evidence_id"] + "}}",
        "metric_ref": "{{metric:" + f"{run['id']}/{spec['metric']}/{spec['split']}" + "}}",
    }
    try:
        source = _source(conn, spec)
        result.update(source)
        reported, unit = _paper_number(source, spec)
        result.update(reported_value=float(reported), canonical_unit=unit)
        metric = row(
            conn.execute(
                "SELECT * FROM run_metrics WHERE run_id=? AND name=? AND split=?",
                (run["id"], spec["metric"], spec["split"]),
            )
        )
        if metric is None or not finite(metric["value"]):
            raise ValueError("missing or nonfinite metric")
        result["verification"] = metric["verification"]
        if run["state"] != "succeeded" or metric["verification"] != "recomputed":
            raise ValueError("run unsuccessful or metric not independently recomputed")
        reproduced, metric_unit = _quantity(str(metric["value"]), metric["unit"])
        result["reproduced_value"] = float(reproduced)
        if unit != metric_unit:
            raise ValueError("units differ; no implicit unit conversion")
        tolerance = Decimal(str(spec["tolerance"]["value"]))
        threshold = tolerance if spec["tolerance"]["kind"] == "absolute" else abs(reported) * tolerance
        delta = abs(reproduced - reported)
        if not finite(threshold) or not finite(delta):
            raise ValueError("comparison exceeds finite numeric range")
        result.update(
            status="match" if delta <= threshold else "deviation",
            absolute_deviation=float(delta),
            threshold=float(threshold),
        )
        if result["status"] == "deviation":
            causes, notes = _parameter_diffs(conn, params, source["version_id"], plan, actual)
            result.update(candidate_causes=causes, parameter_notes=notes)
    except (ValueError, InvalidOperation) as exc:
        result["reason"] = str(exc)
    return result


def compare_run(
    conn: sqlite3.Connection,
    run_id: str,
    items: list[dict[str, Any]],
    *,
    paper_params: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare a completed K5 rerun against paper quotes; tolerances use canonical units (percent -> fraction).

    Selection indices are zero-based in knowledge.text.numbers_in(quote). Parameter/metric semantic mappings
    are explicit caller selections, never inferred. No new execution or approval path is introduced.
    """
    params = paper_params or []
    try:
        jsonschema.validate(items, COMPARE_SCHEMA)
        jsonschema.validate(params, PARAM_SCHEMA)
        if any(not finite(s["tolerance"]["value"]) for s in items):
            raise ValueError("tolerance must be finite")
    except (jsonschema.ValidationError, ValueError) as exc:
        raise ApiError(422, "invalid_comparison", str(exc)) from exc
    with tx(conn):
        run = row(
            conn.execute(
                "SELECT r.*, t.payload, t.kind FROM runs r JOIN tasks t ON t.id=r.task_id WHERE r.id=?", (run_id,)
            )
        )
        if run is None:
            raise not_found("run")
        if run["kind"] != "experiment.run":
            raise ApiError(422, "invalid_comparison", "comparison requires an experiment run")
        payload = jload(run["payload"])
        revision = plans.get_revision(conn, payload["plan_id"], payload["revision"])
        return {
            "run_id": run_id,
            "plan_id": revision["plan_id"],
            "revision": revision["revision"],
            "plan_hash": revision["content_hash"],
            "tolerance_units": "absolute: canonical units; relative: fraction of abs(reported)",
            "items": [
                _compare_item(conn, run, s, params, revision["content"].get("params", {}), payload["params"])
                for s in items
            ],
        }
