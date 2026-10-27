-- core 0011 (ADR-171): fingerprint kinds. A dataset id or manifest hash identifies one dataset and always restricts;
-- sample / group ids ("content") may be shared across datasets and can be attributed to a declared public dataset.
ALTER TABLE dataset_fingerprints ADD COLUMN kind TEXT NOT NULL DEFAULT 'content';
UPDATE dataset_fingerprints SET kind = 'identity'
  WHERE token IN (SELECT lower(id) FROM datasets UNION SELECT lower(manifest_sha256) FROM datasets);
