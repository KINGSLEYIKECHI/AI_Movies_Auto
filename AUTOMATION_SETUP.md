# Automated visual-production path

This upgrade preserves the existing `nigerian_mechanic_past_machine` data.

## One-time database migration

Run `db/init/12_asset_review_pipeline.sql` once against the live
`glm_pipeline` database. It adds an approval-aware asset library and records
the exact assets used as references for every derived shot. It does not delete
or rewrite story, scene, shot, or existing job records.

## Start n8n

Add a long random `N8N_ENCRYPTION_KEY` to `db/.env`, then start the optional
automation overlay from `db/`:

```powershell
docker compose -f docker-compose.yml -f docker-compose.automation.yml up -d --build
```

Open `http://localhost:5678`. n8n uses the same private MySQL service as the
pipeline and should be configured with a MySQL credential using the values in
`db/.env`. It is the review/control interface; local GPU work is not moved
into n8n.

## Consistency workflow

1. Queue and render character, location, and prop reference jobs.
2. Review only those candidate assets in n8n (or with the command below).
3. Approve the canonical visual references.
4. Render shot images. Each is an OpenAI multi-image edit using the approved
   character and location references plus its shot prompt.
5. Render approved shot images to video locally through the existing LTX or
   MiniMax ComfyUI workflow.

```powershell
docker compose exec gateway python run_openai_reference_jobs.py PROJECT_ID
docker compose exec gateway python asset_review.py PROJECT_ID list candidate
docker compose exec gateway python asset_review.py PROJECT_ID approve ASSET_ID
docker compose exec gateway python run_openai_reference_jobs.py PROJECT_ID
```

The second worker run will stop with a clear message whenever a required
reference asset is not approved. It intentionally does not use text-to-image
for that shot as a hidden fallback. Local reference-guided image generation
can be connected later by adding a verified ComfyUI image-to-image workflow.
