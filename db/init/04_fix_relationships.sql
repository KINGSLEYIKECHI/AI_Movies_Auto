-- Additive fix — run manually against an already-running container:
--   docker exec -i glm_mysql mysql -u root -p glm_pipeline < db/init/04_fix_relationships.sql
--
-- Without this, every time a character is echoed back in an episode call
-- (even unchanged), another duplicate relationship row gets inserted.

-- First remove existing duplicates, keeping the lowest id of each group.
DELETE r1 FROM character_relationships r1
INNER JOIN character_relationships r2
WHERE r1.id > r2.id
  AND r1.character_id = r2.character_id
  AND r1.related_character_id = r2.related_character_id;

ALTER TABLE character_relationships
    ADD UNIQUE KEY uq_char_relationship (character_id, related_character_id);
