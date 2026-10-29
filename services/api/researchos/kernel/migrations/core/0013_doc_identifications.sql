-- core 0013 (ADR-233): title-based identification of imported PDFs without DOI/arXiv. Candidate matches are
-- suggestions only; a document is linked to a work only by the owner's confirm.
CREATE TABLE doc_identifications (
  id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL REFERENCES document_versions(version_id),
  candidate_id TEXT NOT NULL REFERENCES lit_candidates(id),
  title_guess TEXT NOT NULL,                     -- the title read from page 1 that was searched
  score REAL NOT NULL,                           -- normalised title similarity, 0..1
  status TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','confirmed','rejected')),
  decided_by TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (version_id, candidate_id)
);
CREATE INDEX idx_doc_identifications_status ON doc_identifications(status, created_at);
