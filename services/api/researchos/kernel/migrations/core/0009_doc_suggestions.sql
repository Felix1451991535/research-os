-- core 0009 (M6): suggestion mode for docs. Agents/models (and users) propose edits; only an owner action applies
-- them as a new revision. A suggestion applies only if its anchor text is still present exactly once.
CREATE TABLE doc_suggestions (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES docs(id),
  base_version INTEGER NOT NULL,
  op TEXT NOT NULL CHECK (op IN ('replace','insert_after','append')),
  anchor TEXT,                                   -- exact text to replace / insert after (NULL for append)
  text TEXT NOT NULL,
  rationale TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '{}',             -- JSON e.g. {"answer_id": ...}
  author TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed','accepted','rejected','conflict')),
  decided_by TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE INDEX idx_doc_suggestions ON doc_suggestions(doc_id, status);
