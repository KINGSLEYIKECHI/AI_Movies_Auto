# Automation rollout

Run these commands from the `db` directory of your existing production checkout,
which contains your real `.env` and Compose volumes. The current changes are in
the original project folder. See [STUDIO_GUIDE.md](STUDIO_GUIDE.md) for the new
production interface and a small first test.

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

Use **Describe** to enter a concept, episode/scene/shot counts, runtime and models.
Compute and edit the prompt, then generate the full plan. In **Prompts**, review
queued image and video prompts before approving and starting automation.

The gateway checks enabled productions every ten seconds, starts one batch at a
time and pauses for review at each stage by default. Approve canonical references,
shot frames, video clips and final exports in **Review**. Use **Exports** for the
assembled files. Pause automation before editing prompts; the active batch finishes.
Replacement versions pause automation and require fresh prompt approval.

Runs return an ID immediately; `/runs/{id}` supplies persisted status and log
output. Use one uvicorn process and avoid concurrent external CLI workers.
Existing projects without studio settings retain the manual worker controls.

Failures stop OpenAI batches. `POST /projects/{project}/jobs/{job_id}/retry`
requeues a failed job; start its worker afterwards. For video failures with a
`.comfy.json` sidecar, inspect that saved ComfyUI prompt ID first: the worker
refuses to resubmit it blindly. After a gateway restart, runs become interrupted;
check running job records and provider outputs before recovering them manually.
Do not automatically reset running jobs: they may still be executing externally.

## Scope and validation

The n8n control workflow is described in `db/n8n/README.md`. The optional schedule
calls `/automation/tick` every five minutes for enabled, planned productions and
respects their review gates. The built-in gateway timer already handles this;
leave the n8n schedule inactive unless needed. Separate wardrobe assets, scene-specific prop assignment, previous
frame continuity, independent local voice/music generation, and precise subtitle
alignment are not yet implemented. Current OpenAI shot references select approved
character identities and locations. LTX/MiniMax workflow-generated audio is retained.
Local image-to-image remains deferred.

Code checks: `python -m unittest discover -s db/gateway/tests -v` and Python syntax
compilation. Docker/MySQL migrations, actual OpenAI generation, ComfyUI GPU renders,
n8n import/execution, and FFmpeg media export must be verified on the production
machine; Docker is unavailable in the development workspace.
