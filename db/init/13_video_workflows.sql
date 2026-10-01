-- Additive, repeatable registration of the supplied API video templates.
UPDATE model_registry SET workflow_template_path='/app/workflows/IMG-video_ltx2_5_i2v.json'
WHERE model_key='ltx-2.5' AND backend='comfyui';
UPDATE model_registry SET workflow_template_path='/app/workflows/video_minimax_h3_i2v.json'
WHERE model_key='minimax-h3' AND backend='comfyui';
