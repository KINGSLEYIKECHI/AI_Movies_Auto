-- Extends the (never-yet-used) jobs table to support the asset-generation
-- phase: character/location/prop REFERENCE images (generated once, reused
-- everywhere that entity appears — this is the "environment/character
-- locking" mechanism), then per-shot images, then per-shot video — with
-- video enforced to require every shot image in the project to be done
-- first (enforced in code, see enqueue_asset_jobs.py).
--
-- Apply manually against an already-running container:
--   docker exec -i glm_mysql mysql -u root -pYOUR_PASSWORD glm_pipeline < db/init/09_asset_jobs.sql

ALTER TABLE jobs
    MODIFY job_type ENUM('story', 'character_reference', 'location_reference',
                          'prop_reference', 'shot_image', 'shot_video', 'audio') NOT NULL,
    ADD COLUMN character_id VARCHAR(128) NULL AFTER shot_id,
    ADD COLUMN location_id  VARCHAR(128) NULL AFTER character_id,
    ADD COLUMN prop_id      VARCHAR(128) NULL AFTER location_id,
    ADD COLUMN prompt       TEXT NULL AFTER job_type,
    ADD FOREIGN KEY (character_id) REFERENCES characters(character_id) ON DELETE CASCADE,
    ADD FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE CASCADE,
    ADD FOREIGN KEY (prop_id) REFERENCES props(prop_id) ON DELETE CASCADE,
    ADD INDEX idx_jobs_type (job_type);
