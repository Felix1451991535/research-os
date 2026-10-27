"""Topic feasibility check (owner order a, ADR-181): can this topic be done with the available data, compute and
time? A project-scope card (cards.py): what the project library says about the topic comes from cited, verified QA
answers; data, compute and time come from recorded facts only (registered datasets and their governance, the
template manifest and installed modules, the GPU probe, durations of comparable runs of a reference plan revision).
Each dimension gets a fixed status - ok | at_risk | blocked | unknown - with its reason; there is no overall score."""

from __future__ import annotations

import importlib.util
import sqlite3
import statistics
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ...kernel.db import jload, rows
from ..execution import datasets
from ..execution.templates import TemplateRegistry
from ..gateway.gateway import Gateway
from . import cards

QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("prior", "Prior work", "What methods and results have been reported for {topic}?"),
    ("data_lit", "Datasets used in the literature", "Which datasets have been used for {topic}, and are they "
     "publicly available?"),
    ("difficulties", "Reported difficulties", "Which difficulties or limitations are reported for {topic}?"),
)
DIMENSIONS = ("literature", "data", "compute", "time")


def _minutes(started: str, ended: str) -> float:
    return (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds() / 60


def template_runs(conn: sqlite3.Connection, template_id: str) -> list[dict[str, Any]]:
    """Succeeded experiment runs whose plan revision uses this template: plan, revision, template hash, dataset,
    device label and wall-clock minutes."""
    out = []
    for r in rows(conn.execute(
            "SELECT r.started_at, r.ended_at, r.device_label, t.payload FROM runs r JOIN tasks t ON t.id=r.task_id "
            "WHERE t.kind='experiment.run' AND r.state='succeeded' AND r.ended_at IS NOT NULL")):
        p = jload(r["payload"])
        rev = conn.execute("SELECT content FROM plan_revisions WHERE plan_id=? AND revision=?",
                           (p.get("plan_id"), p.get("revision"))).fetchone()
        content = jload(rev[0]) if rev else {}
        if content.get("template", {}).get("id") == template_id:
            out.append({"plan_id": p["plan_id"], "revision": int(p["revision"]),
                        "template_hash": content["template"].get("hash"),
                        "dataset_id": (content.get("dataset") or {}).get("id"), "device": r["device_label"],
                        "minutes": _minutes(r["started_at"], r["ended_at"])})
    return out


def _timing(conn: sqlite3.Connection, tid: str | None, tpl_hash: str | None, subject: dict[str, Any]) -> dict[str, Any]:
    """Comparable workload = runs of the reference plan revision (fixed template, dataset, parameters and limits)
    with the template as installed now, a selected dataset and one device. Anything else is history, shown apart and
    never used for the estimate (review #68)."""
    runs = template_runs(conn, tid) if tid else []
    ref = subject.get("reference_plan") or {}
    selected = set(subject.get("dataset_ids") or [])
    comp = [r for r in runs if ref and (r["plan_id"], r["revision"]) == (ref.get("plan_id"), ref.get("revision"))
            and r["template_hash"] == tpl_hash and (r["dataset_id"] in selected if selected else not r["dataset_id"])]
    devices = sorted({str(r["device"]) for r in comp})
    why = ("no reference plan revision given" if not ref else "no succeeded run of the reference plan revision with "
           "the installed template and a selected dataset" if not comp else
           f"runs on several devices ({', '.join(devices)})" if len(devices) > 1 else None)
    mins = [r["minutes"] for r in comp] if why is None else []
    hist = [r["minutes"] for r in runs]
    return {"comparable_runs": len(mins), "median_run_minutes": statistics.median(mins) if mins else None,
            "not_comparable_reason": why, "device": devices[0] if mins else None,
            "history": {"runs": len(hist), "median_minutes": statistics.median(hist) if hist else None}}


def facts(conn: sqlite3.Connection, registry: TemplateRegistry, gpu: dict[str, Any],
          subject: dict[str, Any]) -> dict[str, Any]:
    ds = []
    for did in subject.get("dataset_ids") or []:
        d = datasets.get(conn, did)
        gov = jload(d["governance"]) or {}
        ds.append({"id": d["id"], "name": d["name"], "version": d["version"], "license": d["license"],
                   "egress": gov.get("egress", "local_only"), "group_key": d["group_key"]})
    tid = subject.get("template_id")
    tpl = registry.get(tid) if tid else None
    t = None if tpl is None else {
        "id": tid, "status": tpl.status, "resource_class": tpl.manifest.resource_class,
        "missing_modules": [m for m in tpl.manifest.requires if importlib.util.find_spec(m) is None]}
    return {"datasets": ds, "template_id": tid, "template": t, "gpu_detected": bool(gpu.get("detected")),
            **_timing(conn, tid, tpl.hash if tpl else None, subject),
            "runs_planned": subject.get("runs_planned"), "deadline_days": subject.get("deadline_days")}


def _data(f: dict[str, Any]) -> tuple[str, str]:
    if not f["datasets"]:
        return "blocked", "no registered dataset selected"
    unlicensed = [d["name"] for d in f["datasets"] if not d["license"] or d["license"].lower() == "unknown"]
    ungrouped = [d["name"] for d in f["datasets"] if not d["group_key"]]
    if unlicensed or ungrouped:
        return "at_risk", "; ".join(x for x in (unlicensed and f"licence not recorded: {', '.join(unlicensed)}",
                                                 ungrouped and f"no group key, so no subject-wise split: "
                                                 f"{', '.join(ungrouped)}") if x)
    return "ok", "licence recorded and a group key for subject-wise splits on every dataset"


def _compute(f: dict[str, Any]) -> tuple[str, str]:
    t = f["template"]
    if f["template_id"] is None:
        return "unknown", "no template selected"
    if t is None:
        return "blocked", f"template {f['template_id']} is not installed"
    if t["missing_modules"]:
        return "blocked", f"missing python modules: {', '.join(t['missing_modules'])}"
    if t["resource_class"] == "gpu" and not f["gpu_detected"]:
        return "blocked", "template needs a GPU; none detected"
    if t["status"] != "reviewed":
        return "at_risk", f"template is {t['status']}; the owner must review it before runs"
    return "ok", f"reviewed {t['resource_class']} template, dependencies installed"


def _time(f: dict[str, Any]) -> tuple[str, str]:
    if f["median_run_minutes"] is None:
        h = f["history"]
        return "unknown", (f"no comparable workload: {f['not_comparable_reason']}; history of this template "
                           f"({h['runs']} runs) is not used for the estimate")
    if not f["runs_planned"] or not f["deadline_days"]:
        return "unknown", "planned runs or deadline not given"
    need = f["median_run_minutes"] * f["runs_planned"]
    have = f["deadline_days"] * 24 * 60
    why = (f"{f['runs_planned']} runs x median {f['median_run_minutes']:.1f} min "
           f"({f['comparable_runs']} comparable runs on {f['device']}) "
           f"= {need:.0f} min of serial machine time; {have} min until the deadline (owner work not included)")
    return ("blocked" if need > have else "at_risk" if need > have / 2 else "ok"), why


def assess(f: dict[str, Any], done: dict[str, Any]) -> dict[str, tuple[str, str]]:
    lit = (("ok", "verified, cited prior work in the project library") if done.get("prior", {}).get("cited")
           else ("unknown", "no verified, cited statement on prior work in the project library"))
    return {"literature": lit, "data": _data(f), "compute": _compute(f), "time": _time(f)}


def _tail(conn: sqlite3.Connection, subject: dict[str, Any], done: dict[str, Any]) -> list[tuple[str, str, str]]:
    f = subject["facts"]
    status = assess(f, done)
    data = "\n".join(f"- {d['name']} {d['version']}: licence {d['license'] or 'not recorded'}, egress {d['egress']}, "
                     f"group key {d['group_key'] or 'none'}" for d in f["datasets"]) or "- none selected"
    table = "\n".join(["| Dimension | Status | Reason |", "|---|---|---|",
                       *(f"| {k} | {status[k][0]} | {status[k][1]} |" for k in DIMENSIONS)])
    return [("facts", data, "registered dataset records"),
            ("status", table, "computed from recorded facts and verified answers only")]


SPEC = cards.CardSpec("feasibility", "Feasibility", QUESTIONS,
                      (("facts", "Data on record"), ("status", "Status by dimension")), "Owner decision", _tail,
                      scope="project")


def create(conn: sqlite3.Connection, gw: Gateway, doc_kinds: Any, registry: TemplateRegistry,
           gpu: Callable[[], dict[str, Any]], subject: dict[str, Any], *, project_id: str, actor: str,
           progress: dict[str, Any] | None = None, checkpoint: Callable[[], None] = lambda: None) -> dict[str, Any]:
    progress = progress if progress is not None else {}
    if "facts" not in progress:  # observed once per intent, so a resumed check reports the same facts
        progress["facts"] = facts(conn, registry, gpu(), subject)
        checkpoint()
    out = cards.create(conn, gw, doc_kinds, SPEC, {**subject, "facts": progress["facts"]}, project_id=project_id,
                       actor=actor, progress=progress, checkpoint=checkpoint)
    return {**out, "facts": progress["facts"],
            "status": {k: {"status": s, "reason": r} for k, (s, r) in assess(progress["facts"], out["fields"]).items()}}
