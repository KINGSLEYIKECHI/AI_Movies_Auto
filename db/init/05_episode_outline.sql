-- Additive — lightweight series-level plan, one row per episode, storing
-- ONLY which characters/locations belong in that episode (not full detail).
-- Populated by a SERIES_OUTLINE-stage response, read by
-- generate_episode_prompt.py to scope reference context per episode
-- instead of resending the entire cast/location list every time.

CREATE TABLE IF NOT EXISTS episode_outline (
    episode_id      VARCHAR(128) PRIMARY KEY,
    project_id      VARCHAR(64) NOT NULL,
    episode_number  INT NOT NULL,
    title           VARCHAR(255),
    summary         TEXT,
    character_ids   JSON,
    location_ids    JSON,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    UNIQUE KEY uq_outline_project_episode (project_id, episode_number)
) ENGINE=InnoDB;
