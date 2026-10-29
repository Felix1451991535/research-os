-- core 0015 (Review #99): a confirmed suggestion is final only once the document is linked to its work. A confirm
-- whose process ended between the decision and the link (linked_at NULL) can be confirmed again to finish it.
ALTER TABLE doc_identifications ADD COLUMN linked_at TEXT;
-- confirms finished before this migration emitted library.identified for the document version
UPDATE doc_identifications SET linked_at = (SELECT min(e.ts) FROM events e WHERE e.kind = 'library.identified'
                                            AND e.entity_id = doc_identifications.version_id)
WHERE status = 'confirmed';
