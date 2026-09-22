-- Additive fix — run manually against an already-running container:
--   docker exec -i glm_mysql mysql -u root -p glm_pipeline < db/init/03_fix_constraints.sql
--
-- Without this, re-generating the same episode number twice creates two
-- untied continuity_snapshots rows instead of updating one, and "latest
-- snapshot" queries could pick either on a tie.

ALTER TABLE continuity_snapshots
    ADD UNIQUE KEY uq_snapshot_project_episode (project_id, episode_number);
