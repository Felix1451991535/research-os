-- core 0016 (Review #101): the confirm attempt that currently owns a confirmed suggestion's linking. A retry takes
-- it over; a failed attempt re-opens the suggestions only while it still owns them, so an overlapping attempt that
-- is still linking keeps the decision.
ALTER TABLE doc_identifications ADD COLUMN attempt_id TEXT;
