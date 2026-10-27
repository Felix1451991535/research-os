-- core 0004 (M4): pre-registered 6-stage study protocol (screen -> shrink -> search -> confirm -> freeze -> final_eval)
CREATE TABLE studies (
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL REFERENCES plans(id),
  base_revision INTEGER NOT NULL,
  current_revision INTEGER NOT NULL,
  protocol TEXT NOT NULL,                 -- JSON, fixed at creation (pre-registered); never edited
  protocol_hash TEXT NOT NULL,
  stage TEXT NOT NULL,                    -- screen | search | confirm | control | awaiting_final_approval | final | completed | stopped
  data TEXT NOT NULL DEFAULT '{}',        -- stage outputs (analysis, shrink, frozen config, ...)
  configs_evaluated INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Canary sweep (ARCHITECTURE §6.7): built-in fault-injection self-test of the detectors.
CREATE TABLE canaries (
  id TEXT PRIMARY KEY,
  plans TEXT NOT NULL,                    -- JSON {"group": plan_id, "leak": plan_id}
  template_hash TEXT NOT NULL,
  started_at TEXT,
  created_at TEXT NOT NULL
);
