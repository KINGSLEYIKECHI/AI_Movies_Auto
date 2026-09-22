-- Additive only — does not modify any table from 01_schema.sql.
-- Stores a compact "where things stand" snapshot after each episode loads,
-- so generate_episode_prompt.py can feed forward continuity without
-- resending the full history of every prior episode.

CREATE TABLE IF NOT EXISTS continuity_snapshots (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    project_id          VARCHAR(64) NOT NULL,
    episode_id          VARCHAR(64) NOT NULL,
    episode_number      INT NOT NULL,
    ending_state        TEXT,
    character_status    JSON,
    prop_status         JSON,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    INDEX idx_snapshot_project (project_id, episode_number)
) ENGINE=InnoDB;
