"""Fingerprint index for the egress gate (ADR-159/162/171): tokens that identify a dataset's content, lower-cased,
by kind - `identity` (dataset id, manifest hash: unique, always restrict) and `content` (sample ids, group ids: may
be shared across datasets) - plus a marker row written only when the content tokens were really read. A dataset
without the marker has an incomplete index: indexing is retried on every check, and until it succeeds a restricted
dataset counts as possibly present in any prompt (review #53)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ...kernel.db import Row, jload, rows, tx

MIN_FINGERPRINT = 6  # shorter ids ("s01") would also match ordinary words in prompts
INDEXED = "\x00indexed"  # marker row (never matches prompt text)


def content_tokens(directory: Path, manifest: dict[str, Any]) -> set[str]:
    """Sample and group ids of the dataset table; OSError / ValueError when the table cannot be read."""
    table = directory / str(manifest.get("table", ""))
    if not (table.is_file() and table.suffix == ".npz"):
        raise FileNotFoundError("dataset table not readable")
    import numpy as np

    out: set[str] = set()
    with np.load(table, allow_pickle=False) as npz:
        for key in ("sample_id", "group"):
            if key in npz.files:
                out.update(str(v).lower() for v in npz[key].tolist())
    return {t for t in out if len(t) >= MIN_FINGERPRINT}


def store(conn: sqlite3.Connection, did: str, manifest_sha: str, content: set[str] | None) -> None:
    """Identity tokens always; content tokens and the completeness marker only when the table was read."""
    identity = {t.lower() for t in (did, manifest_sha) if len(t) >= MIN_FINGERPRINT}
    entries = [(t, did, "identity") for t in identity]
    if content is not None:
        entries += [(t, did, "content") for t in content - identity] + [(INDEXED, did, "marker")]
    with tx(conn, join=True):
        conn.executemany("INSERT OR IGNORE INTO dataset_fingerprints(token, dataset_id, kind) VALUES (?,?,?)",
                         entries)


def index(conn: sqlite3.Connection, ds: Row) -> bool:
    """(Re)build one dataset's index; idempotent. Returns whether it is complete."""
    try:
        directory = Path((jload(ds["storage"]) or {})["path"])
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        content: set[str] | None = content_tokens(directory, manifest)
    except (OSError, ValueError, KeyError, TypeError):
        content = None
    store(conn, ds["id"], ds["manifest_sha256"], content)
    return content is not None


def reindex_missing(conn: sqlite3.Connection) -> list[Row]:
    """Retry every dataset without the marker; returns those still incomplete."""
    todo = rows(conn.execute("SELECT * FROM datasets WHERE id NOT IN "
                             "(SELECT dataset_id FROM dataset_fingerprints WHERE token=?)", (INDEXED,)))
    return [ds for ds in todo if not index(conn, ds)]
