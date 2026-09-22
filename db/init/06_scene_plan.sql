-- Additive — the scene-level planning layer between episode_outline and
-- shots. scene_plan holds the 20-beats-per-episode map; scene_continuity
-- holds the scene-to-scene "where things stand" pointer within one episode,
-- same pattern as continuity_snapshots but one level finer-grained.

CREATE TABLE IF NOT EXISTS scene_plan (
    scene_id        VARCHAR(128) PRIMARY KEY,
    project_id      VARCHAR(64) NOT NULL,
    episode_id      VARCHAR(128) NOT NULL,
    scene_number    INT NOT NULL,
    beat            TEXT,
    location_id     VARCHAR(128),
    character_ids   JSON,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    UNIQUE KEY uq_sceneplan_episode_number (episode_id, scene_number)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS scene_continuity (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    project_id      VARCHAR(64) NOT NULL,
    episode_id      VARCHAR(128) NOT NULL,
    scene_id        VARCHAR(128) NOT NULL,
    scene_number    INT NOT NULL,
    ending_state    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    UNIQUE KEY uq_scenecontinuity_episode_number (episode_id, scene_number)
) ENGINE=InnoDB;
