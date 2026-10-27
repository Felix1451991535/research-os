-- core 0002 (M1): K7 knowledge (works, documents, pages, chunks, FTS, evidence) + K6 gateway (catalog, prices,
-- FX, calls, ledger, certification). ARCHITECTURE core 1.0.0, additive.
CREATE TABLE works (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  kind TEXT NOT NULL DEFAULT 'article',
  title TEXT NOT NULL DEFAULT '',
  authors TEXT NOT NULL DEFAULT '[]',           -- JSON list of names
  year INTEGER,
  venue TEXT,
  language TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',           -- per-field values with source (reconciliation keeps both)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE identifiers (
  work_id TEXT NOT NULL REFERENCES works(id),
  scheme TEXT NOT NULL,                          -- doi | arxiv | pmid | isbn | cnki | zotero | sha256 | ...
  value TEXT NOT NULL,                           -- normalized (doi lower-case)
  source TEXT NOT NULL,
  PRIMARY KEY (scheme, value)
);
CREATE INDEX idx_identifiers_work ON identifiers(work_id);

CREATE TABLE document_versions (
  version_id TEXT PRIMARY KEY,
  work_id TEXT NOT NULL REFERENCES works(id),
  sha256 TEXT NOT NULL UNIQUE,                   -- same bytes => same version (parse reused)
  stored_path TEXT NOT NULL,                     -- relative to data_dir (raw/<sha[:2]>/<sha>.pdf)
  original_name TEXT NOT NULL,
  size INTEGER NOT NULL,
  mime TEXT NOT NULL,
  license TEXT,
  parser_version TEXT,
  parse_state TEXT NOT NULL DEFAULT 'pending'
    CHECK (parse_state IN ('pending','parsing','parsed','partial','failed','unsupported','needs_ocr')),
  parse_error TEXT,
  recovery_action TEXT,
  page_count INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_docver_work ON document_versions(work_id);

CREATE TABLE pages (
  version_id TEXT NOT NULL REFERENCES document_versions(version_id),
  page_index INTEGER NOT NULL,                   -- physical, 0-based
  page_label TEXT,                               -- printed label if known
  width REAL, height REAL, rotation INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL DEFAULT '',
  text_sha256 TEXT NOT NULL,
  char_count INTEGER NOT NULL,
  quality TEXT NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok','needs_proofreading','empty','garbled')),
  flags TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (version_id, page_index)
);

CREATE TABLE chunks (
  chunk_id TEXT PRIMARY KEY,                     -- hash(version, parser_version, page, offset)
  version_id TEXT NOT NULL REFERENCES document_versions(version_id),
  parser_version TEXT NOT NULL,
  page_index INTEGER NOT NULL,
  char_start INTEGER NOT NULL,                   -- offsets into pages.text
  char_end INTEGER NOT NULL,
  bbox TEXT,                                     -- JSON [x0,y0,x1,y1] in PDF user space, origin bottom-left
  section_role TEXT,                             -- title|abstract|introduction|methods|results|discussion|references|other
  chunk_kind TEXT NOT NULL DEFAULT 'text',       -- text|caption|table|equation|figure
  text TEXT NOT NULL,
  text_sha256 TEXT NOT NULL,
  quality TEXT NOT NULL DEFAULT 'ok',
  seq INTEGER NOT NULL
);
CREATE INDEX idx_chunks_version ON chunks(version_id, page_index, seq);

-- trigram tokenizer: CJK-capable substring search (ADR-008)
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

-- K7 single validated write path: a row exists only after a deterministic quote match.
CREATE TABLE evidence (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  version_id TEXT NOT NULL REFERENCES document_versions(version_id),
  chunk_id TEXT REFERENCES chunks(chunk_id),
  page_index INTEGER NOT NULL,
  char_start INTEGER NOT NULL,
  char_end INTEGER NOT NULL,
  quote TEXT NOT NULL,
  quote_sha256 TEXT NOT NULL,
  page_text_sha256 TEXT NOT NULL,                -- digest of the page text the quote was verified against
  parser_version TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'source_fact'
    CHECK (kind IN ('source_fact','author_conclusion','model_inference','hypothesis')),
  trust TEXT NOT NULL DEFAULT 'machine_checked'
    CHECK (trust IN ('failed','unverified','machine_checked','human_checked')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_evidence_version ON evidence(version_id);

CREATE TABLE answers (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  question TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('answered','insufficient_material','not_configured','failed','refused')),
  body TEXT NOT NULL,                            -- JSON: sentences with evidence ids + kinds, dropped sentences
  trust_composition TEXT NOT NULL DEFAULT '{}',
  call_ids TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

-- K6 catalog: aliases -> provider model ids; prices and FX are config DATA, unverified until the owner confirms.
CREATE TABLE model_aliases (
  alias TEXT PRIMARY KEY,                        -- ROUTINE_TEXT, DOCUMENT_OCR, ..., PREMIUM_AGENT
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  tier TEXT NOT NULL CHECK (tier IN ('routine','premium','fallback','top')),
  enabled INTEGER NOT NULL DEFAULT 1,
  context_window INTEGER,
  max_output_tokens INTEGER,
  updated_at TEXT NOT NULL
);

CREATE TABLE model_prices (
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  currency TEXT NOT NULL,
  input_per_mtok REAL,
  output_per_mtok REAL,
  official_input_per_mtok REAL,
  official_output_per_mtok REAL,
  effective_date TEXT,
  source TEXT,
  verified INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (provider, model_id)
);

CREATE TABLE fx_rates (
  currency TEXT PRIMARY KEY,
  cny_per_unit REAL NOT NULL,
  source TEXT NOT NULL,
  as_of TEXT NOT NULL,
  verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE model_calls (
  id TEXT PRIMARY KEY,
  task_type TEXT NOT NULL,
  alias TEXT,
  provider TEXT,
  model_id TEXT,
  prompt_name TEXT,
  prompt_hash TEXT,
  status TEXT NOT NULL CHECK (status IN ('routed','rejected','in_flight','ok','error','unknown')),
  routing_before TEXT NOT NULL DEFAULT '{}',
  routing_after TEXT NOT NULL DEFAULT '{}',
  input_tokens INTEGER, output_tokens INTEGER,
  error_code TEXT,
  project_id TEXT, task_id TEXT, trace_id TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE ledger_entries (
  id TEXT PRIMARY KEY,
  call_id TEXT REFERENCES model_calls(id),
  cost_kind TEXT NOT NULL DEFAULT 'model_call' CHECK (cost_kind IN ('model_call','compute','other')),
  state TEXT NOT NULL CHECK (state IN ('reserved','settled','unknown','released')),
  month TEXT NOT NULL,                           -- YYYY-MM, Asia/Shanghai
  reserved_cny REAL NOT NULL,
  actual_cny REAL,
  provider_response_id TEXT UNIQUE,              -- dedupes settlement (never double-charge)
  price_snapshot TEXT NOT NULL,                  -- prices + FX + dates used for the estimate
  project_id TEXT, task_id TEXT,
  created_at TEXT NOT NULL,
  settled_at TEXT
);
CREATE INDEX idx_ledger_month ON ledger_entries(month, state);

CREATE TABLE eval_runs (
  id TEXT PRIMARY KEY,
  suite TEXT NOT NULL,
  model_id TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  metrics TEXT NOT NULL,
  passed INTEGER NOT NULL,
  override INTEGER NOT NULL DEFAULT 0,           -- owner override (labelled)
  created_at TEXT NOT NULL
);
CREATE INDEX idx_eval_cert ON eval_runs(model_id, prompt_hash, passed);

-- one paid call in flight across API + worker processes (baseline §8); holder expires to survive crashes
CREATE TABLE gateway_slot (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  holder TEXT,
  until TEXT
);
INSERT INTO gateway_slot(id, holder, until) VALUES (1, NULL, NULL);
