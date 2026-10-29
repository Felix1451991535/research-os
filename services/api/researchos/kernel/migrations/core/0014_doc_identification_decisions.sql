-- core 0014 (Review #100): the decision that set a suggestion's status. A failed confirm re-opens exactly the
-- suggestions its own decision changed, not every one decided in the same millisecond.
ALTER TABLE doc_identifications ADD COLUMN decision_id TEXT;
