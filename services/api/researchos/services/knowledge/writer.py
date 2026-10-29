"""Host KnowledgeWriter for importers: hard-identifier dedupe (DOI > PMID > arXiv), idempotency keys, events,
provenance. Originals go through the normal content-addressed import path (parsing queued)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from ...contracts.datasources import BibRecord
from ...kernel.db import jdump, new_id, row, tx, utcnow
from ...kernel.events import emit, prov_link
from ..content.blobs import BlobStore
from ..orchestration.registry import KindRegistry
from .importer import import_document
from .metadata import normalize_doi

HARD = ("doi", "pmid", "arxiv", "zotero")  # zotero = "<users|groups>/<id>/<key>", library-scoped


def _norm(scheme: str, value: str) -> str:
    return normalize_doi(value) if scheme == "doi" else value.strip()


class DbKnowledgeWriter:
    def __init__(
        self,
        conn: sqlite3.Connection,
        blobs: BlobStore,
        kinds: KindRegistry,
        *,
        plugin: str,
        actor: str,
        project_id: str | None,
        max_bytes: int,
    ) -> None:
        self.conn, self.blobs, self.kinds = conn, blobs, kinds
        self.plugin, self.actor, self.project_id, self.max_bytes = plugin, actor, project_id, max_bytes

    def _replay(self, scope: str, key: str) -> str | None:
        r = row(self.conn.execute("SELECT response_json FROM idempotency WHERE scope=? AND key=?", (scope, key)))
        return str(r["response_json"]).strip('"') if r else None

    def upsert_work(self, record: BibRecord, *, idempotency_key: str) -> tuple[str, bool]:
        scope = f"plugin.{self.plugin}.work"
        prior = self._replay(scope, idempotency_key)
        if prior:
            return prior, False
        ids: list[tuple[str, str]] = [(s, _norm(s, v)) for s, v in record.identifiers]
        with tx(self.conn):
            existing = None
            for s, v in ids:
                if s in HARD:
                    r = row(self.conn.execute("SELECT work_id FROM identifiers WHERE scheme=? AND value=?", (s, v)))
                    existing = existing or (r["work_id"] if r else None)
            wid, created = (existing, False) if existing else (new_id("work"), True)
            if created:
                now = utcnow()
                self.conn.execute(
                    "INSERT INTO works(id,project_id,kind,title,authors,year,venue,metadata,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        wid,
                        self.project_id,
                        record.kind,
                        record.title,
                        jdump(list(record.authors)),
                        record.year,
                        record.venue,
                        jdump(
                            {
                                "source": record.source,
                                "source_id": record.source_id,
                                "url": record.url,
                                "abstract": record.abstract,
                                "license": record.license,
                                "license_source": record.license_source,
                            }
                        ),
                        now,
                        now,
                    ),
                )
            for s, v in ids + [(record.source, record.source_id)]:
                self.conn.execute(
                    "INSERT OR IGNORE INTO identifiers(work_id,scheme,value,source) VALUES (?,?,?,?)",
                    (wid, s, v, f"plugin:{self.plugin}"),
                )
            seq = emit(
                self.conn,
                "work.imported" if created else "work.reused",
                actor=self.actor,
                entity_type="work",
                entity_id=wid,
                project_id=self.project_id,
                data={"plugin": self.plugin, "source": record.source},
            )
            prov_link(self.conn, wid, "attributedTo", f"plugin:{self.plugin}", seq)
            self.conn.execute(
                "INSERT INTO idempotency(key,scope,request_hash,status_code,response_json,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (idempotency_key, scope, "-", 200, jdump(wid), utcnow()),
            )
        return str(wid), created

    def attach_original(
        self, work_id: str, data: bytes, filename: str, *, license: str | None, idempotency_key: str,
        guard: Callable[[], None] | None = None,
    ) -> str:
        """`guard` (host callers only, not in the plugin contract) runs first inside the relink transaction; if it
        raises, nothing is relinked (identify_title fences stale confirms with it, Review #103)."""
        scope = f"plugin.{self.plugin}.original"
        prior = self._replay(scope, idempotency_key)
        if prior:
            return prior
        res = import_document(
            self.conn,
            self.blobs,
            self.kinds,
            data=data,
            filename=filename,
            project_id=self.project_id,
            actor=self.actor,
            max_bytes=self.max_bytes,
        )
        vid = str(res["version"]["version_id"])
        with tx(self.conn):
            if guard is not None:
                guard()
            old = row(self.conn.execute("SELECT work_id FROM document_versions WHERE version_id=?", (vid,)))
            if old and old["work_id"] != work_id:  # bytes belong to this Work (importer knows the record)
                self.conn.execute(
                    "UPDATE document_versions SET work_id=?, license=? WHERE version_id=?", (work_id, license, vid)
                )
                self.conn.execute("UPDATE identifiers SET work_id=? WHERE work_id=?", (work_id, old["work_id"]))
                self.conn.execute(
                    "DELETE FROM works WHERE id=? AND NOT EXISTS (SELECT 1 FROM document_versions WHERE work_id=?)",
                    (old["work_id"], old["work_id"]),
                )
            self.conn.execute(
                "INSERT INTO idempotency(key,scope,request_hash,status_code,response_json,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (idempotency_key, scope, "-", 200, jdump(vid), utcnow()),
            )
        return vid
