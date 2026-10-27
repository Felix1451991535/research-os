-- core 0008 (M5): deep research pipeline runs (sub-questions -> multi-round search -> screening -> import -> read ->
-- page-cited synthesis with contradiction flags -> report). Human checkpoints are K3 approvals / user decisions.
CREATE TABLE deep_research (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  question TEXT NOT NULL,
  settings TEXT NOT NULL,                        -- JSON {sources, rounds, page_size, use_model_screening}
  plan TEXT,                                     -- JSON {sub_questions: [{q, queries}]}
  review_id TEXT REFERENCES lit_reviews(id),
  stage TEXT NOT NULL CHECK (stage IN ('planning','awaiting_plan_approval','searching','awaiting_screening',
                                       'awaiting_import_approval','reading','synthesizing','completed','stopped')),
  data TEXT NOT NULL DEFAULT '{}',               -- stage outputs (runs, selection hash, works, answers, claims, report)
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
