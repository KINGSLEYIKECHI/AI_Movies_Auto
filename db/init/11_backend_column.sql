-- Adds a 'backend' column to model_registry so the worker knows HOW to
-- dispatch each model (local ComfyUI graph vs a cloud API), not just
-- WHICH model. Backfills existing rows as comfyui (their original
-- assumption), then registers OpenAI's image API as a new backend.

ALTER TABLE model_registry
    ADD COLUMN backend ENUM('comfyui', 'openai_api', 'ltx_desktop') NOT NULL DEFAULT 'comfyui' AFTER model_type;

INSERT INTO model_registry (model_key, model_type, backend, display_name, checkpoint_filename, valid_durations, available, notes) VALUES
    ('gpt-image-2.5-sunburst', 'image', 'openai_api', 'OpenAI GPT Image 2.5 (Sunburst)', NULL, NULL, TRUE,
     'Cloud API, not local — requires OPENAI_API_KEY in .env. model_key matches the exact OpenAI API model string, used directly, no translation needed.')
ON DUPLICATE KEY UPDATE backend=VALUES(backend), notes=VALUES(notes);
