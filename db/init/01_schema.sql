-- Schema mirrors the GLM output contract field-for-field so the loader
-- can insert straight from parsed JSON with no translation logic.

CREATE TABLE IF NOT EXISTS projects (
    project_id          VARCHAR(64)  PRIMARY KEY,
    title                VARCHAR(255),
    genre                VARCHAR(100),
    subgenre             VARCHAR(100),
    tone                 VARCHAR(255),
    target_audience      VARCHAR(100),
    logline              TEXT,
    premise              TEXT,
    themes               JSON,
    world_rules          JSON,
    setting              VARCHAR(255),
    time_period          VARCHAR(100),
    visual_style         TEXT,
    color_language       VARCHAR(255),
    narrative_pov        VARCHAR(100),
    major_conflicts      JSON,
    stakes               TEXT,
    ending               TEXT,
    continuity_rules     JSON,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS characters (
    character_id         VARCHAR(64)  PRIMARY KEY,
    project_id           VARCHAR(64)  NOT NULL,
    name                 VARCHAR(255),
    age                  VARCHAR(50),
    role                 VARCHAR(100),
    personality          TEXT,
    goals                TEXT,
    fears                TEXT,
    motivations          TEXT,
    appearance           JSON,
    default_wardrobe_id  VARCHAR(64),
    accessories          JSON,
    prop_ids             JSON,
    voice_characteristics TEXT,
    emotional_traits     JSON,
    character_arc        TEXT,
    current_story_status TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS character_relationships (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    character_id         VARCHAR(64) NOT NULL,
    related_character_id VARCHAR(64) NOT NULL,
    relationship         VARCHAR(255),
    FOREIGN KEY (character_id) REFERENCES characters(character_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS locations (
    location_id           VARCHAR(64)  PRIMARY KEY,
    project_id             VARCHAR(64)  NOT NULL,
    name                   VARCHAR(255),
    architecture           TEXT,
    geography              TEXT,
    interior_exterior      VARCHAR(100),
    time_of_day_appearance VARCHAR(255),
    weather                VARCHAR(255),
    lighting               TEXT,
    color_palette          VARCHAR(255),
    recurring_props        JSON,
    atmosphere             TEXT,
    visual_landmarks       JSON,
    continuity_rules       TEXT,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wardrobe (
    wardrobe_id          VARCHAR(64)  PRIMARY KEY,
    character_id         VARCHAR(64)  NOT NULL,
    outfit_name          VARCHAR(255),
    top                  VARCHAR(255),
    bottom               VARCHAR(255),
    shoes                VARCHAR(255),
    accessories          JSON,
    colors               JSON,
    materials            JSON,
    `condition`          VARCHAR(255),
    weather_suitability  VARCHAR(255),
    story_period         VARCHAR(100),
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (character_id) REFERENCES characters(character_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS props (
    prop_id               VARCHAR(64)  PRIMARY KEY,
    project_id             VARCHAR(64)  NOT NULL,
    name                   VARCHAR(255),
    appearance             TEXT,
    material               VARCHAR(255),
    color                  VARCHAR(100),
    size                   VARCHAR(100),
    owner_character_id     VARCHAR(64)  NULL,
    current_location_id    VARCHAR(64)  NULL,
    story_significance     TEXT,
    current_status         VARCHAR(255),
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (owner_character_id) REFERENCES characters(character_id) ON DELETE SET NULL,
    FOREIGN KEY (current_location_id) REFERENCES locations(location_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS episodes (
    episode_id           VARCHAR(64)  PRIMARY KEY,
    project_id            VARCHAR(64)  NOT NULL,
    title                  VARCHAR(255),
    objective              TEXT,
    beginning              TEXT,
    development            TEXT,
    conflict               TEXT,
    turning_point          TEXT,
    climax                 TEXT,
    ending                 TEXT,
    cliffhanger            TEXT,
    character_ids          JSON,
    location_ids           JSON,
    continuity_notes       TEXT,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS scenes (
    scene_id              VARCHAR(64)  PRIMARY KEY,
    episode_id             VARCHAR(64)  NOT NULL,
    purpose                TEXT,
    location_id             VARCHAR(64)  NULL,
    time                    VARCHAR(100),
    weather                 VARCHAR(100),
    character_ids           JSON,
    wardrobe_ids            JSON,
    prop_ids                JSON,
    action                  TEXT,
    emotional_state         VARCHAR(255),
    dialogue_summary        TEXT,
    conflict                TEXT,
    beginning_state         TEXT,
    ending_state            TEXT,
    continuity_requirements TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE,
    FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS shots (
    shot_id              VARCHAR(64)  PRIMARY KEY,
    scene_id              VARCHAR(64)  NOT NULL,
    shot_type              VARCHAR(100),
    framing                 VARCHAR(255),
    camera_position         VARCHAR(255),
    lens                    VARCHAR(100),
    camera_movement         VARCHAR(255),
    character_ids           JSON,
    character_action        TEXT,
    facial_expression       VARCHAR(255),
    environment             TEXT,
    lighting                TEXT,
    atmosphere               VARCHAR(255),
    duration_seconds         INT,
    sound_effects            JSON,
    music_direction          TEXT,
    image_prompt             TEXT,
    video_prompt             TEXT,
    negative_prompt          TEXT,
    continuity_notes         TEXT,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (scene_id) REFERENCES scenes(scene_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS shot_dialogue (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    shot_id                VARCHAR(64) NOT NULL,
    character_id            VARCHAR(64),
    line                    TEXT,
    line_order              INT,
    FOREIGN KEY (shot_id) REFERENCES shots(shot_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Used by the GPU broker/resume-watcher step that comes next. One row per
-- unit of generation work (a shot's image, a shot's video clip, etc.), so
-- the watcher can find "the last incomplete step" per project.
CREATE TABLE IF NOT EXISTS jobs (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    project_id             VARCHAR(64) NOT NULL,
    episode_id             VARCHAR(64) NULL,
    scene_id                VARCHAR(64) NULL,
    shot_id                 VARCHAR(64) NULL,
    job_type                 ENUM('story','char_image','scene_image','video','audio') NOT NULL,
    status                    ENUM('queued','running','done','failed') NOT NULL DEFAULT 'queued',
    model_used                VARCHAR(100),
    output_path                VARCHAR(500),
    error                       TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    INDEX idx_jobs_status (status),
    INDEX idx_jobs_project (project_id)
) ENGINE=InnoDB;
