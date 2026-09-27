-- Separation of concerns: story generation (MySQL + GLM/Qwen) never
-- hardcodes which image/video model or workflow gets used downstream.
-- model_registry is the catalog of available models; project_model_config
-- locks ONE choice per project, once, so every shot in that project stays
-- consistent (same model, same duration) rather than drifting shot to shot.

CREATE TABLE IF NOT EXISTS model_registry (
    model_key           VARCHAR(64) PRIMARY KEY,
    model_type          ENUM('image', 'video') NOT NULL,
    display_name        VARCHAR(255),
    checkpoint_filename VARCHAR(255),  -- reference only, matches your D:\comfyui\models file
    -- Video models only. NULL/empty for image models (no duration concept).
    -- PLACEHOLDER values where the model's real discrete duration support
    -- hasn't been confirmed yet — verify against your actual workflow
    -- before relying on these for a real render.
    valid_durations     JSON,
    workflow_template_path VARCHAR(500),  -- set once you have a working exported ComfyUI workflow; NULL = not wired up yet
    available           BOOLEAN DEFAULT TRUE,  -- FALSE = registered but not actually downloaded/present yet
    notes                TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

INSERT INTO model_registry (model_key, model_type, display_name, checkpoint_filename, valid_durations, available, notes) VALUES
    ('flux1-dev', 'image', 'Flux.1 Dev', 'flux1-dev.safetensors', NULL, TRUE, 'Confirmed present in D:\\comfyui\\models\\diffusion_models'),
    ('flux1-schnell', 'image', 'Flux.1 Schnell', 'flux1-schnell-fp8.safetensors', NULL, TRUE, 'Confirmed present, checkpoints folder'),
    ('flux2-klein-4b', 'image', 'Flux-2 Klein 4B', 'flux-2-klein-base-4b.safetensors', NULL, TRUE, 'Confirmed present'),
    ('flux2-klein-9b', 'image', 'Flux-2 Klein 9B', 'flux-2-klein-base-9b-fp8.safetensors', NULL, TRUE, 'Confirmed present'),
    ('sdxl-base', 'image', 'SDXL Base 1.0', 'sd_xl_base_1.0.safetensors', NULL, TRUE, 'Confirmed present, checkpoints folder'),
    ('qwen-image', 'image', 'Qwen-Image', NULL, NULL, FALSE, 'NOT YET DOWNLOADED — requested but not present in D:\\comfyui\\models as of last inventory check'),
    ('wan2.2', 'video', 'Wan 2.2 TI2V 5B', 'wan2.2_ti2v_5B_fp16.safetensors', '[5, 8, 10]', TRUE, 'PLACEHOLDER durations — Wan2.2 appears frame-count/FPS driven rather than fixed presets; confirm against your actual workflow before relying on this'),
    ('minimax-h3', 'video', 'MiniMax H3', 'minimax_h3_fl2va_pruned_int8_convrot.safetensors', '[6, 10]', TRUE, 'PLACEHOLDER durations, unconfirmed'),
    ('ltx-2.3', 'video', 'LTX 2.3', NULL, '[8, 10]', TRUE, 'From LTX Desktop, not ComfyUI models folder — path TBD'),
    ('ltx-2.5', 'video', 'LTX 2.5', NULL, '[8, 10]', TRUE, 'From LTX Desktop, not ComfyUI models folder — path TBD')
ON DUPLICATE KEY UPDATE display_name=VALUES(display_name);

CREATE TABLE IF NOT EXISTS project_model_config (
    project_id           VARCHAR(64) PRIMARY KEY,
    image_model_key      VARCHAR(64) NOT NULL,
    video_model_key      VARCHAR(64) NOT NULL,
    locked_duration_seconds INT NOT NULL,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (image_model_key) REFERENCES model_registry(model_key),
    FOREIGN KEY (video_model_key) REFERENCES model_registry(model_key)
) ENGINE=InnoDB;
