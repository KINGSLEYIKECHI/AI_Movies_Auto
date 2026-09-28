-- Updates based on real, confirmed hardware testing rather than general
-- documentation — user confirmed LTX 2.5 Fast runs locally on their
-- RTX 5080 (16GB). Supersedes the cautious placeholder from 08_model_registry.sql.

UPDATE model_registry
SET available = TRUE,
    notes = 'CONFIRMED working locally by user on RTX 5080 16GB (LTX 2.5 Fast variant specifically — not the heavier Pro/standard variant). Dispatch mechanism (LTX Desktop local API vs ComfyUI) still TBD.'
WHERE model_key = 'ltx-2.5';

INSERT INTO model_registry (model_key, model_type, display_name, checkpoint_filename, valid_durations, available, notes) VALUES
    ('z-image-turbo', 'image', 'Z-Image Turbo', 'z_image_turbo_bf16.safetensors', NULL, FALSE,
     'Text encoder (qwen_3_4b.safetensors) and VAE (ae.safetensors) already present in D:\\comfyui\\models — only the diffusion checkpoint itself needs downloading, from Comfy-Org/z_image_turbo on HuggingFace.')
ON DUPLICATE KEY UPDATE notes=VALUES(notes), available=VALUES(available);
