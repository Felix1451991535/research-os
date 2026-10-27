"""physio_ptbxl: PTB-XL 12-lead ECG (PhysioNet ptb-xl 1.0.3), one sample per 10 s record.

Labels: diagnostic superclass (NORM/MI/STTC/CD/HYP) aggregated from scp_codes via scp_statements.csv exactly as in
the PTB-XL reference code (all diagnostic statements, no likelihood threshold). Only records with exactly ONE
superclass are kept (single-label table); multi-superclass and no-superclass records are counted, not guessed.
Split: the official patient-level strat_fold - folds 1-8 train, 9 search_val, 10 test (confirm_fold may move one
training fold to confirm_val). A patient found in two folds aborts the build.
"""

from __future__ import annotations

import ast
import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from researchos.contracts.datasources import DatasetBuildContext, DatasetDescriptor, DataSourceSpec

from . import wfdb
from .common import LeakageError, Table, check_cancel, class_counts, descriptor, split_spec
from .fetch import HOST, Upstream

BASE = "https://physionet.org/files/ptb-xl/1.0.3/"
VERSION = "1.0.3"
SUPERCLASSES = ("CD", "HYP", "MI", "NORM", "STTC")

SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sampling_rate": {"enum": [100, 500], "description": "records100 (default) or records500"},
        "folds": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1, "maximum": 10},
            "minItems": 1,
            "uniqueItems": True,
            "description": "strat_fold values to include (default 1-10)",
        },
        "max_records_per_fold": {
            "type": ["integer", "null"],
            "minimum": 1,
            "description": "cap per fold, lowest ecg_id first (default: no cap)",
        },
        "confirm_fold": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": 8,
            "description": "training fold to use as confirm_val (default: none)",
        },
        "license_override": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Row:
    ecg_id: int
    patient: str
    fold: int
    label: int
    path: str


def _int(text: str, what: str) -> int:
    try:
        value = float(text)
    except ValueError as e:
        raise ValueError(f"ptbxl_database.csv: invalid {what} {text!r}") from e
    if not value.is_integer():
        raise ValueError(f"ptbxl_database.csv: non-integer {what} {text!r}")
    return int(value)


def diagnostic_map(scp_csv: str) -> dict[str, str]:
    """scp code -> diagnostic superclass, for statements with diagnostic == 1."""
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(scp_csv)):
        code = row.get("") or row.get("Unnamed: 0")
        diag = (row.get("diagnostic") or "").strip()
        cls = (row.get("diagnostic_class") or "").strip()
        if code and diag and float(diag) == 1.0 and cls:
            out[code.strip()] = cls
    return out


def scp_codes(text: str) -> dict[str, float]:
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"ptbxl_database.csv: unparseable scp_codes {text[:60]!r}") from e
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError(f"ptbxl_database.csv: scp_codes is not a mapping: {text[:60]!r}")
    return {str(k): float(v) for k, v in value.items()}


def parse_database(db_csv: str, diag: Mapping[str, str], rate: int) -> tuple[list[Row], dict[str, int]]:
    """Eligible single-superclass rows + exclusion counts. Verifies that strat_fold is patient-level."""
    col = "filename_lr" if rate == 100 else "filename_hr"
    rows: list[Row] = []
    counts = {"multi_superclass": 0, "no_superclass": 0}
    fold_of: dict[str, int] = {}
    for r in csv.DictReader(io.StringIO(db_csv)):
        patient, fold = f"ptbxl_p{_int(r['patient_id'], 'patient_id')}", _int(r["strat_fold"], "strat_fold")
        if fold_of.setdefault(patient, fold) != fold:
            raise LeakageError(f"{patient} appears in strat_fold {fold_of[patient]} and {fold}")
        classes = {diag[c] for c in scp_codes(r["scp_codes"]) if c in diag}
        if len(classes) != 1:
            counts["multi_superclass" if classes else "no_superclass"] += 1
            continue
        rows.append(Row(_int(r["ecg_id"], "ecg_id"), patient, fold, SUPERCLASSES.index(classes.pop()), r[col]))
    return rows, counts


def select(rows: list[Row], settings: Mapping[str, Any]) -> list[Row]:
    folds = set(settings.get("folds") or range(1, 11))
    cap = settings.get("max_records_per_fold")
    out: list[Row] = []
    for f in sorted(folds):
        in_fold = sorted((r for r in rows if r.fold == f), key=lambda r: r.ecg_id)
        out.extend(in_fold[: int(cap)] if cap else in_fold)
    return out


def split_name(fold: int, confirm_fold: int | None) -> str:
    if fold == 10:
        return "test"
    if fold == 9:
        return "search_val"
    return "confirm_val" if fold == confirm_fold else "train"


class PtbXlProvider:
    spec = DataSourceSpec(
        "physio_ptbxl",
        "0.1.0",
        "dataset",
        "PTB-XL 1.0.3 12-lead ECG, diagnostic superclass labels, official patient-level strat_fold split",
        hosts=(HOST,),
        settings_schema=SETTINGS_SCHEMA,
    )

    def _record(self, up: Upstream, row: Row) -> tuple[wfdb.Header, Any]:
        folder = row.path.rsplit("/", 1)[0]
        header = wfdb.parse_header(up.get_text(f"{row.path}.hea"))
        files = {s.file_name: up.get(f"{folder}/{s.file_name}") for s in header.signals}
        return header, wfdb.to_physical(header, wfdb.read_digital(header, files))

    def build(self, ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> DatasetDescriptor:
        up = Upstream(ctx.http, BASE)
        up.load_checksums()
        lic = up.license(settings)
        rate = int(settings.get("sampling_rate", 100))
        rows, excluded = parse_database(
            up.get_text("ptbxl_database.csv"), diagnostic_map(up.get_text("scp_statements.csv")), rate
        )
        chosen = select(rows, settings)
        confirm = settings.get("confirm_fold")
        table, leads = Table(), None
        assign: dict[str, set[str]] = {s: set() for s in ("train", "search_val", "confirm_val", "test")}
        for i, row in enumerate(chosen):
            check_cancel(ctx)
            header, phys = self._record(up, row)
            names = tuple(s.description for s in header.signals)
            if leads is None:
                leads = names
            if names != leads or header.fs != rate:
                raise wfdb.WfdbError(f"ecg {row.ecg_id}: leads {names} / fs {header.fs} differ from the dataset")
            table.add(phys, row.label, row.patient, f"ptbxl/{row.ecg_id}")
            assign[split_name(row.fold, confirm)].add(row.patient)
            ctx.progress("records", i + 1, len(chosen))
        label_map = dict(enumerate(SUPERCLASSES))
        extra = {
            "dataset": "PTB-XL",
            "upstream_version": VERSION,
            "signal_type": "ECG",
            "sampling_rate_hz": rate,
            "leads": list(leads or ()),
            "units": "mV",
            "x_layout": "X[N, samples, leads] (10 s records)",
            "label_map": {str(k): v for k, v in label_map.items()},
            "class_counts": class_counts(table.y, label_map),
            "label_rule": "diagnostic superclass via scp_statements.csv; single-superclass records only",
            "excluded_records": excluded,
            "n_records": len(chosen),
            "split": split_spec(assign, table.group),
            "split_source": "official strat_fold: 1-8 train, 9 search_val, 10 test"
            + (f", fold {confirm} -> confirm_val" if confirm else ""),
        }
        return descriptor("physio_ptbxl", VERSION, up, lic, table, ctx.out_dir, extra)
