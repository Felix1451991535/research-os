-- core 0017 (Review #102, #104): confirms interrupted before 0014 are unfinished (linked_at NULL) but carry no
-- decision id, so a failed retry could not re-open them. Give each its own decision id. Rejections are left alone:
-- before 0014 nothing recorded which rejections a confirm made - a sibling it rejected and a separate rejection by
-- the same actor in the same millisecond look alike - so none is re-opened on a guess (a failed retry re-opens the
-- confirmed suggestion only).
UPDATE doc_identifications SET decision_id = 'dec_legacy_' || id
WHERE status = 'confirmed' AND linked_at IS NULL AND decision_id IS NULL;
