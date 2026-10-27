-- core 0005: model calls record how they were sent (direct vendor API or the configured relay). Additive.
ALTER TABLE model_calls ADD COLUMN transport TEXT NOT NULL DEFAULT 'direct';
