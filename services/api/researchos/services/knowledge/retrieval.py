"""FTS5 (trigram) retriever over chunks + metadata. Vectors are an optional plugin, not core (baseline §5)."""
from __future__ import annotations

import re
import sqlite3

from ...contracts.knowledge import SearchHit

_TOKEN = re.compile(r"[\w一-鿿\-]{3,}", re.UNICODE)


def build_query(q: str) -> str | None:
    """Trigram FTS needs >= 3 chars per term; each term is quoted (no FTS syntax injection), OR-combined."""
    terms = list(dict.fromkeys(t.lower() for t in _TOKEN.findall(q)))[:12]
    if not terms:
        return None
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


class FtsRetriever:
    name = "fts5-trigram"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def search(self, query: str, *, project_id: str | None, limit: int = 10,
               work_id: str | None = None) -> list[SearchHit]:
        fts = build_query(query)
        if fts is None:
            return []
        sql = ("SELECT c.chunk_id, c.version_id, w.id AS work_id, w.title, c.page_index, p.page_label, c.char_start, "
               "c.char_end, c.text, bm25(chunks_fts) AS score, c.section_role, c.quality "
               "FROM chunks_fts JOIN chunks c ON c.rowid=chunks_fts.rowid "
               "JOIN document_versions v ON v.version_id=c.version_id JOIN works w ON w.id=v.work_id "
               "JOIN pages p ON p.version_id=c.version_id AND p.page_index=c.page_index "
               "WHERE chunks_fts MATCH ? AND c.parser_version=v.parser_version")
        args: list[object] = [fts]
        if project_id:
            sql += " AND w.project_id=?"
            args.append(project_id)
        if work_id:  # one paper only (per-paper note cards)
            sql += " AND w.id=?"
            args.append(work_id)
        sql += " ORDER BY score LIMIT ?"
        args.append(limit)
        return [SearchHit(**dict(r)) for r in self.conn.execute(sql, args)]
