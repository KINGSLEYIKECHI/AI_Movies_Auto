-- Fix: shots.scene_id can now legitimately reference EITHER scenes.scene_id
-- (the legacy nested EPISODE_FULL path) OR scene_plan.scene_id (the new
-- per-scene SHOT_PLAN path). A foreign key can only point at one table, so
-- drop the constraint entirely — correctness here is guaranteed at the
-- application level (scoped IDs, scene_plan lookups) instead.
--
-- Apply manually against an already-running container:
--   docker exec -i glm_mysql mysql -u root -p glm_pipeline < db/init/07_fix_shots_fk.sql

ALTER TABLE shots DROP FOREIGN KEY shots_ibfk_1;
