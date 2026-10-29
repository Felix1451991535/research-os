-- core 0012 (ADR-231): one advance per deep research run at a time. advance_owner/advance_until are a short lease
-- taken atomically before a stage runs; stage changes are compare-and-swap on the stage they leave.
ALTER TABLE deep_research ADD COLUMN advance_owner TEXT;
ALTER TABLE deep_research ADD COLUMN advance_until TEXT;
