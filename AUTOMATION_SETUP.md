# Automation rollout

Run these commands from the `db` directory of your production checkout (the
checkout containing your real `.env` and existing Compose project). Do not
start a second stack from this worktree: its default volume names differ.
Copy/merge the updated source into the existing production checkout first.

## Back up and migrate the existing story

Keep `glm_pipeline.sql`. Make a fresh backup using credentials already inside
MySQL; these commands do not print your password:

```powershell
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --routines --triggers "$MYSQL_DATABASE" > /tmp/film-before-automation.sql'
if ($LASTEXITCODE -ne 0) { throw 'Backup failed; stop here' }
docker compose cp mysql:/tmp/film-before-automation.sql ./film-before-automation.sql
if ($LASTEXITCODE -ne 0) { throw 'Backup copy failed; stop here' }
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot "$MYSQL_DATABASE" < /docker-entrypoint-initdb.d/12_asset_review_pipeline.sql'
if ($LASTEXITCODE -ne 0) { throw 'Migration 12 failed; stop here' }
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot "$MYSQL_DATABASE" < /docker-entrypoint-initdb.d/13_video_workflows.sql'
if ($LASTEXITCODE -ne 0) { throw 'Migration 13 failed; stop here' }
```

Existing databases need only 12 and 13 for this upgrade; do not replay all old
migrations or import the whole story backup over your live database. Migration
12 and 13 preserve story data and are repeatable. Fresh initialization now
skips the already-present job columns and absent legacy shots FK.

## Start the API and n8n

Keep your existing `OPENAI_API_KEY`, host paths, and MySQL credentials in
`db/.env`. Add `N8N_ENCRYPTION_KEY` once and retain its value permanently.
`COMFYUI_URL` defaults to `http://host.docker.internal:8188`.

```powershell
docker compose -f docker-compose.yml -f docker-compose.automation.yml up -d --build
Invoke-RestMethod http://localhost:8000/health
```

The automation overlay starts uvicorn on the gateway. Base Compose alone still
uses the original command-driven gateway. Containers use `http://gateway:8000`;
your browser uses `http://localhost:8000`.

- Visual generation/review panel: http://localhost:8000/review
- API explorer: http://localhost:8000/docs
- n8n: http://localhost:5678

n8n stores its own configuration in SQLite on the existing `glm_n8n_data`
volume; production stories remain in MySQL. Current n8n no longer supports
MySQL as its internal storage backend. If an older n8n instance has actually
saved workflows/credentials in MySQL, export/migrate that n8n state before
switching storage; this configuration does not migrate it automatically.
Never use `docker compose down -v` against your production stack.

## Produce and review

Select `nigerian_mechanic_past_machine` in the review panel:

1. Queue references. Generate a small OpenAI batch and inspect its progress.
2. Approve the character/location/prop candidates. Reject poor results, select
   rejected assets, and use **Queue new version**, then start the worker again.
3. Queue shot images and generate with OpenAI. Missing approved references
   block the image worker. Local canonical generation requires a configured
   `project_model_fallbacks` entry; it never generates reference-free shot images.
4. Approve shot frames. All shot frames must be approved before queuing video.
5. Queue videos and render locally. Your locked project video model must be
   `ltx-2.5` or `minimax-h3` with its registered supplied workflow path. ComfyUI
   must already have the workflow's models and custom nodes installed.
6. Approve video clips. Enter the full project-scoped episode ID (as stored in
   `scene_plan.episode_id`) and assemble the episode. Assembly uses that episode's
   approved videos, preserves audio, normalizes clips to 720x1280 at 24 fps, and
   creates `episode.mp4` and a separate `episode.srt` under the final folder.
7. Review the final export candidate.

Default batch size is one to make the first paid render reviewable. Runs return
an ID immediately; `/runs/{id}` supplies persisted status and recent log output.
One worker runs at a time in the gateway to avoid overlapping GPU requests.
Run uvicorn with one process. External CLI workers must not be run concurrently
with it. Worker submissions are explicit; review approval does not trigger new
paid generation automatically.

Failures stop OpenAI batches. `POST /projects/{project}/jobs/{job_id}/retry`
requeues a failed job; start its worker afterwards. For video failures with a
`.comfy.json` sidecar, inspect that saved ComfyUI prompt ID first: the worker
refuses to resubmit it blindly. After a gateway restart, runs become interrupted;
check running job records and provider outputs before recovering them manually.
Do not automatically reset running jobs: they may still be executing externally.

## Scope and validation

The n8n control workflow is described in `db/n8n/README.md`. The optional n8n schedule advances the existing planned story one batch at a
time and pauses at candidate reviews, rejected assets, or failed/running jobs.
Import `film-automation-schedule.json` and activate it only when ready for
automatic paid OpenAI batches; it checks every five minutes. It remains inactive
in the source. Separate wardrobe assets, scene-specific prop assignment, previous
frame continuity, independent local voice/music generation, and precise subtitle
alignment are not yet implemented. Current OpenAI shot references select approved
character identities and locations. LTX/MiniMax workflow-generated audio is retained.
Local image-to-image remains deferred.

Code checks: `python -m unittest discover -s db/gateway/tests -v` and Python syntax
compilation. Docker/MySQL migrations, actual OpenAI generation, ComfyUI GPU renders,
n8n import/execution, and FFmpeg media export must be verified on the production
machine; Docker is unavailable in the development workspace.
