-- core 0003 (M2): K1 datasets, immutable plan revisions, artifacts, run metrics, health signals, catalog revision.
CREATE TABLE datasets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  source TEXT NOT NULL,
  license TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,                 -- hash of the dataset manifest (which hashes every file)
  storage TEXT NOT NULL,                         -- JSON {kind: internal|external, path}
  group_key TEXT,                                -- e.g. subject: splits never share groups
  governance TEXT NOT NULL DEFAULT '{}',         -- JSON: license notes, ethics status, data class
  created_at TEXT NOT NULL,
  UNIQUE (name, version)
);

CREATE TABLE plans (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  title TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Immutable: rows are inserted, never updated (trigger). Approval binds to content_hash.
CREATE TABLE plan_revisions (
  plan_id TEXT NOT NULL REFERENCES plans(id),
  revision INTEGER NOT NULL,
  content TEXT NOT NULL,                         -- JSON: hypothesis, predictions, template, params/space, data, split, limits
  content_hash TEXT NOT NULL,
  parent_revision INTEGER,
  change_kind TEXT NOT NULL DEFAULT 'initial',   -- initial | narrowing | widening | other
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (plan_id, revision)
);
CREATE TRIGGER plan_revisions_immutable BEFORE UPDATE ON plan_revisions
BEGIN SELECT RAISE(ABORT, 'plan revisions are immutable; create a new revision'); END;

CREATE TABLE artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  path TEXT NOT NULL,                            -- relative to data_dir
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  kind TEXT NOT NULL,                            -- manifest | metrics | predictions | model | log | report | other
  created_at TEXT NOT NULL,
  UNIQUE (run_id, path)
);

CREATE TABLE run_metrics (
  run_id TEXT NOT NULL REFERENCES runs(id),
  name TEXT NOT NULL,
  split TEXT NOT NULL,
  value REAL,
  unit TEXT,
  verification TEXT NOT NULL CHECK (verification IN ('recomputed','invariant_checked','self_reported','unverified','failed')),
  reported_value REAL,                           -- what the template claimed (kept for audit)
  PRIMARY KEY (run_id, name, split)
);

CREATE TABLE health_signals (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  detector TEXT NOT NULL,                        -- id@version
  severity TEXT NOT NULL CHECK (severity IN ('info','warn','fail')),
  rule TEXT NOT NULL,
  value TEXT NOT NULL DEFAULT '{}',
  selector TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_health_run ON health_signals(run_id);

-- Revision fence (HARDENING #7): templates/plugins/policy catalog revision observed by a run at claim time only.
ALTER TABLE runs ADD COLUMN catalog_revision INTEGER;
ALTER TABLE runs ADD COLUMN run_dir TEXT;
INSERT INTO settings_kv(key, value_json, updated_at) VALUES ('catalog.revision', '1', '1970-01-01T00:00:00+00:00');
