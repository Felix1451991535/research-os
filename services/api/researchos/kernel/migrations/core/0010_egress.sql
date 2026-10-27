-- core 0010 (ADR-159): egress gate. Tokens that identify a dataset's content (dataset id, manifest hash, sample ids,
-- group ids) so the gateway can detect dataset-derived text in a prompt even when the caller did not declare it.
CREATE TABLE dataset_fingerprints (
  token TEXT NOT NULL,
  dataset_id TEXT NOT NULL REFERENCES datasets(id),
  PRIMARY KEY (token, dataset_id)
) WITHOUT ROWID;
