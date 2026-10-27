-- core 0001: K1 store, identity, K2 orchestration, K3 approvals, K8 docs (notes). ARCHITECTURE.md core 1.0.0
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE devices (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  paired_via TEXT NOT NULL CHECK (paired_via IN ('setup','password','pairing_code')),
  is_host INTEGER NOT NULL DEFAULT 0,        -- registered from a loopback client
  user_agent TEXT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT,
  revoked_at TEXT
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,            -- sha256 of the bearer token; raw token never stored
  user_id TEXT NOT NULL REFERENCES users(id),
  device_id TEXT NOT NULL REFERENCES devices(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  reauth_at TEXT,                             -- last password re-entry (step-up for remote approvals)
  revoked_at TEXT
);
CREATE INDEX idx_sessions_device ON sessions(device_id);

CREATE TABLE pairing_codes (
  id TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL UNIQUE,
  created_by_device TEXT NOT NULL REFERENCES devices(id),
  device_name TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);

CREATE TABLE idempotency (
  key TEXT NOT NULL,
  scope TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (scope, key)
);
CREATE INDEX idx_idempotency_created ON idempotency(created_at);

CREATE TABLE events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,                        -- device:<id> | worker:<id> | system | user:<id>
  kind TEXT NOT NULL,
  entity_type TEXT,
  entity_id TEXT,
  project_id TEXT,
  correlation_id TEXT,
  data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_events_entity ON events(entity_type, entity_id);
CREATE INDEX idx_events_project ON events(project_id, seq);
-- append-only: forbid UPDATE/DELETE on events
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TABLE prov_links (
  subject TEXT NOT NULL,
  relation TEXT NOT NULL CHECK (relation IN ('wasDerivedFrom','wasGeneratedBy','attributedTo','used')),
  object TEXT NOT NULL,
  event_seq INTEGER REFERENCES events(seq),
  created_at TEXT NOT NULL,
  PRIMARY KEY (subject, relation, object)
);

CREATE TABLE event_cursors (
  consumer TEXT PRIMARY KEY,
  last_seq INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE settings_kv (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  goal TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','archived')),
  next_step TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- generic registered entity types (K1) and typed relations
CREATE TABLE entities (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id),
  data TEXT NOT NULL,
  trust TEXT NOT NULL DEFAULT 'unverified'
    CHECK (trust IN ('failed','unverified','machine_checked','human_checked')),
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_entities_type ON entities(type, project_id);

CREATE TABLE relations (
  src TEXT NOT NULL,
  type TEXT NOT NULL,
  dst TEXT NOT NULL,
  attrs TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  PRIMARY KEY (src, type, dst)
);
CREATE INDEX idx_relations_dst ON relations(dst, type);

-- K8 authored documents (notes in M0). version = server revision for optimistic concurrency.
CREATE TABLE docs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  conflict_of TEXT REFERENCES docs(id),
  client_operation_id TEXT,                   -- dedupes offline re-sync of the same edit
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_docs_conflict_op ON docs(conflict_of, client_operation_id)
  WHERE conflict_of IS NOT NULL AND client_operation_id IS NOT NULL;
CREATE INDEX idx_docs_project ON docs(project_id, kind);

CREATE TABLE doc_revisions (
  doc_id TEXT NOT NULL REFERENCES docs(id),
  version INTEGER NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (doc_id, version)
);

-- K2 orchestration
CREATE TABLE workers (
  id TEXT PRIMARY KEY,
  host TEXT NOT NULL,
  pid INTEGER NOT NULL,
  version TEXT NOT NULL,
  started_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  stopped_at TEXT
);

CREATE TABLE approvals (
  id TEXT PRIMARY KEY,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  subject_hash TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '{}',
  limits TEXT NOT NULL DEFAULT '{}',
  approved_by_device TEXT NOT NULL REFERENCES devices(id),
  reauth INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  revoked_at TEXT
);
CREATE INDEX idx_approvals_subject ON approvals(subject_type, subject_id);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id),
  parent_id TEXT REFERENCES tasks(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  trace_id TEXT NOT NULL,                      -- one trace per top-level task; children inherit (HARDENING #18)
  state TEXT NOT NULL CHECK (state IN ('draft','awaiting_approval','queued','running','cancelling',
                                       'succeeded','failed','cancelled','interrupted','blocked')),
  payload TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  resource_class TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  approval_id TEXT REFERENCES approvals(id),
  attempt INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  lease_owner TEXT,
  lease_expires_at TEXT,
  lease_generation INTEGER NOT NULL DEFAULT 0,
  not_before TEXT,
  cancel_requested_at TEXT,
  stage TEXT,
  progress TEXT NOT NULL DEFAULT '{}',
  result TEXT,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_tasks_state ON tasks(state, resource_class, priority, created_at);
CREATE INDEX idx_tasks_parent ON tasks(parent_id);

CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  attempt INTEGER NOT NULL,
  lease_generation INTEGER NOT NULL,
  worker_id TEXT NOT NULL REFERENCES workers(id),
  trace_id TEXT NOT NULL,
  state TEXT NOT NULL,
  pid INTEGER,                                 -- controlled child process, if any
  pid_create_time REAL,
  device_label TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  result TEXT,
  UNIQUE (task_id, attempt)
);
