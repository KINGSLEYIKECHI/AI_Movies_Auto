-- Adds an approval-aware asset library without changing or deleting the
-- existing production data. Apply ONCE to an existing glm_pipeline database.
-- Fresh databases should run this after 11_backend_column.sql.

CREATE TABLE IF NOT EXISTS asset_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    project_id VARCHAR(64) NOT NULL,
    job_id INT NULL,
    asset_type ENUM('character_reference','wardrobe_reference','location_reference',
                    'prop_reference','shot_image','shot_video','audio','subtitle','final_render') NOT NULL,
    entity_id VARCHAR(128) NULL,
    output_path VARCHAR(500) NOT NULL,
    status ENUM('candidate','approved','rejected','superseded') NOT NULL DEFAULT 'candidate',
    generation_backend VARCHAR(32) NULL,
    generation_model VARCHAR(100) NULL,
    metadata JSON NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL,
    INDEX idx_asset_project_type_status (project_id, asset_type, status),
    INDEX idx_asset_entity (project_id, entity_id, status),
    UNIQUE KEY uq_asset_job_path (job_id, output_path)
) ENGINE=InnoDB;

-- Each generated job declares the approved visual assets it relied on. This
-- makes regeneration reproducible and lets n8n present the exact references
-- used for any frame.
CREATE TABLE IF NOT EXISTS job_asset_references (
    job_id INT NOT NULL,
    asset_id BIGINT NOT NULL,
    reference_role ENUM('character_identity','wardrobe','location','prop','previous_shot','style') NOT NULL,
    reference_order INT NOT NULL DEFAULT 1,
    PRIMARY KEY (job_id, asset_id, reference_role),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id) REFERENCES asset_records(id) ON DELETE RESTRICT,
    INDEX idx_reference_asset (asset_id)
) ENGINE=InnoDB;

-- A project keeps its primary locked image model, while this table provides
-- ordered local fallbacks without changing that primary creative decision.
CREATE TABLE IF NOT EXISTS project_model_fallbacks (
    project_id VARCHAR(64) NOT NULL,
    model_type ENUM('image','video') NOT NULL,
    model_key VARCHAR(64) NOT NULL,
    priority INT NOT NULL DEFAULT 1,
    PRIMARY KEY (project_id, model_type, model_key),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (model_key) REFERENCES model_registry(model_key),
    INDEX idx_fallback_order (project_id, model_type, priority)
) ENGINE=InnoDB;

-- Map the supplied local Flux workflow to the registered local model. The
-- Docker automation overlay mounts this folder at /app/workflows.
UPDATE model_registry
SET workflow_template_path = '/app/workflows/image_flux2_klein_text_to_image_API.json'
WHERE model_key = 'flux2-klein-4b';

-- Preserve completed assets from the already-running pipeline as candidates;
-- approve them in n8n after visually checking them.
INSERT IGNORE INTO asset_records
    (project_id, job_id, asset_type, entity_id, output_path, status, generation_model)
SELECT project_id, id, job_type,
       COALESCE(character_id, location_id, prop_id, shot_id), output_path,
       'candidate', model_used
FROM jobs
WHERE status = 'done'
  AND job_type IN ('character_reference','location_reference','prop_reference','shot_image','shot_video','audio')
  AND output_path IS NOT NULL;
