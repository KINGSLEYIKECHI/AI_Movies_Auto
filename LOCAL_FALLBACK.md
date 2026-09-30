# Local canonical-reference fallback

OpenAI remains the primary image model for reference-guided shot images. If
its API credits are unavailable, register a local Flux model as an image
fallback without changing the project's primary model lock:

```sql
INSERT INTO project_model_fallbacks (project_id, model_type, model_key, priority)
VALUES ('PROJECT_ID', 'image', 'flux2-klein-4b', 1);
```

Set `COMFYUI_URL=http://host.docker.internal:8188` in `db/.env` if ComfyUI
uses its standard local API port, then run:

```powershell
docker compose exec gateway python run_comfyui_reference_jobs.py PROJECT_ID
```

The worker patches the supplied Flux 2 Klein API workflow at runtime, submits
it through ComfyUI's local HTTP API, downloads the output to the project
folder, and creates a review candidate. It supports character, location, and
prop reference jobs only. It will not use local text-to-image as a fallback
for derived shot frames until a verified local image-to-image/reference graph
is deliberately connected.
