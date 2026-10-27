-- core 0007 (M5): K4 Effects. Plans are immutable once proposed (ops + hash); the journal is append-only (intent row
-- before each op, result row after) and drives crash recovery and undo.
CREATE TABLE effect_plans (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  kind TEXT NOT NULL,                            -- library_organize | file_ops
  title TEXT NOT NULL,
  ops TEXT NOT NULL,                             -- JSON list of typed ops with preconditions
  plan_hash TEXT NOT NULL,
  diff TEXT NOT NULL,                            -- JSON dry-run summary shown before approval
  state TEXT NOT NULL CHECK (state IN ('proposed','applying','applied','paused','interrupted','failed','undoing',
                                       'undone','undo_conflict')),
  blocked_reason TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TRIGGER effect_plans_immutable BEFORE UPDATE OF ops, plan_hash, kind ON effect_plans
BEGIN SELECT RAISE(ABORT, 'effect plans are immutable; propose a new plan'); END;

CREATE TABLE effect_journal (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id TEXT NOT NULL REFERENCES effect_plans(id),
  op_index INTEGER NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('intent','done','failed','blocked','skipped','recovered','undo_intent',
                                       'undone','undo_conflict')),
  detail TEXT NOT NULL DEFAULT '{}',
  actor TEXT NOT NULL,
  at TEXT NOT NULL
);
CREATE INDEX idx_effect_journal ON effect_journal(plan_id, op_index, seq);
CREATE TRIGGER effect_journal_no_update BEFORE UPDATE ON effect_journal
BEGIN SELECT RAISE(ABORT, 'effect journal is append-only'); END;
CREATE TRIGGER effect_journal_no_delete BEFORE DELETE ON effect_journal
BEGIN SELECT RAISE(ABORT, 'effect journal is append-only'); END;
