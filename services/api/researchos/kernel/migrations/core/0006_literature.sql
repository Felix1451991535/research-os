-- core 0006 (M5): literature agent. Candidates found by searches (deduped by hard identifier, every source sighting
-- kept), search run log (PRISMA "identified" counts), saved searches + alerts, systematic-review screening log
-- (append-only), citekeys, retraction status on works. Additive.
CREATE TABLE lit_candidates (
  id TEXT PRIMARY KEY,
  dedupe_key TEXT NOT NULL UNIQUE,               -- doi:<v> | pmid:<v> | arxiv:<v> | zotero:<v> | src:<source>:<id>
  record TEXT NOT NULL,                          -- JSON BibRecord as first seen
  provenance TEXT NOT NULL DEFAULT '[]',         -- JSON [{source, source_id, run_id, at}] every sighting
  work_id TEXT REFERENCES works(id),             -- set once imported
  retracted INTEGER,                             -- NULL = unknown (never assumed 0)
  retraction_source TEXT,
  retraction_checked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_lit_candidates_work ON lit_candidates(work_id);

CREATE TABLE lit_search_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  saved_search_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('search','references','cited_by','author')),
  query TEXT NOT NULL,                           -- JSON
  sources TEXT NOT NULL,                         -- JSON list
  per_source TEXT NOT NULL DEFAULT '{}',         -- JSON {source: {returned, total, warnings, error}}
  candidate_ids TEXT NOT NULL DEFAULT '[]',
  new_candidate_ids TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE saved_searches (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('query','author')),
  query TEXT NOT NULL,                           -- JSON {text, filters} | {author}
  sources TEXT NOT NULL,                         -- JSON list of datasource names
  cadence TEXT NOT NULL CHECK (cadence IN ('manual','daily','weekly')),
  review_id TEXT,                                -- new candidates are added to this review (auto-screen rules apply)
  auto_import INTEGER NOT NULL DEFAULT 0,        -- metadata of included candidates (owner opt-in)
  pdf_download INTEGER NOT NULL DEFAULT 0,       -- OA PDFs with explicit open licence (owner opt-in)
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT,
  next_run_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE alerts (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                            -- new_papers | retraction | source_error
  subject_id TEXT,
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  read_at TEXT
);
CREATE INDEX idx_alerts_unread ON alerts(read_at, created_at);

CREATE TABLE lit_reviews (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  title TEXT NOT NULL,
  question TEXT NOT NULL,
  criteria TEXT NOT NULL,                        -- JSON {include: [...], exclude: [...], rules: [...]}
  criteria_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE lit_review_items (
  review_id TEXT NOT NULL REFERENCES lit_reviews(id),
  candidate_id TEXT NOT NULL REFERENCES lit_candidates(id),
  search_run_id TEXT,
  added_at TEXT NOT NULL,
  PRIMARY KEY (review_id, candidate_id)
);

CREATE TABLE screening_decisions (
  id TEXT PRIMARY KEY,
  review_id TEXT NOT NULL REFERENCES lit_reviews(id),
  candidate_id TEXT NOT NULL REFERENCES lit_candidates(id),
  stage TEXT NOT NULL CHECK (stage IN ('title_abstract','full_text')),
  decision TEXT NOT NULL CHECK (decision IN ('include','exclude','maybe')),
  reason TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('decision','suggestion')),   -- suggestions (rules/models) never count as decisions
  actor TEXT NOT NULL,                           -- user:<id> | rule:<id> | model:<alias> | system
  basis TEXT NOT NULL DEFAULT '{}',              -- JSON: rule, model call id, evidence ids, criteria_hash
  created_at TEXT NOT NULL
);
CREATE INDEX idx_screening ON screening_decisions(review_id, candidate_id, stage, created_at);
CREATE TRIGGER screening_no_update BEFORE UPDATE ON screening_decisions
BEGIN SELECT RAISE(ABORT, 'screening log is append-only'); END;
CREATE TRIGGER screening_no_delete BEFORE DELETE ON screening_decisions
BEGIN SELECT RAISE(ABORT, 'screening log is append-only'); END;

CREATE TABLE citekeys (
  work_id TEXT PRIMARY KEY REFERENCES works(id),
  citekey TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

ALTER TABLE works ADD COLUMN retraction TEXT;     -- JSON {retracted, source, notice_url, checked_at}; NULL = unchecked
