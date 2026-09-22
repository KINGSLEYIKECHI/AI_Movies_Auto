# GLM AI Film Production Pipeline

Fully containerized: MySQL, phpMyAdmin, Ollama (GPU), and a "gateway"
container running every Python script. Nothing runs as a native Windows
process except Docker itself and your browser. Generated project files
(images, prompts, JSON) still land on your Windows filesystem via mounted
volumes, so you can see progress live in Explorer.

## Folder structure

```
GLM-Firms/                              <- your working root (e.g. D:\AI\GLM-Firms)
├── Modelfile                           Source of truth for the model's schema contract (edit here)
├── GLM_Test_Prompt.txt                 One-shot test prompt (full bible + 1 episode)
├── GLM_Test_Prompt_Small.txt           Tiny test prompt (project bible only)
│
└── db\                                 Run every command from THIS folder
    ├── docker-compose.yml              Defines all 4 containers: mysql, phpmyadmin, ollama, gateway
    ├── .env.example                    Copy to .env and fill in before first run
    ├── .env                            (you create this — holds real passwords + host paths)
    ├── init\
    │   ├── 01_schema.sql                Core tables — auto-run by MySQL on first container startup only
    │   ├── 02_continuity.sql            continuity_snapshots table — same, first-startup only
    │   └── 03_fix_constraints.sql       Uniqueness fix for continuity_snapshots — apply manually, see below
    └── gateway\                         Build context for the gateway container
        ├── Dockerfile
        ├── requirements.txt
        ├── run_glm_prompt.py             Replaces Run_GLM_Test.ps1 — sends a prompt to GLM, validates, saves
        ├── load_story_to_db.py           Loads a GLM output JSON into MySQL + creates the project folder tree
        ├── generate_episode_prompt.py    Builds the next episode's prompt from what's already in MySQL
        ├── GLM_Test_Prompt.txt           Baked into the image at build time
        ├── GLM_Test_Prompt_Small.txt     Baked into the image at build time
        └── outputs\                      Mounted back to host — GLM_Test_Output.json lands here, visible in Explorer
```

**On editing prompts:** `GLM_Test_Prompt.txt` / `GLM_Test_Prompt_Small.txt`
inside `gateway\` are baked into the container image at build time. If you
edit them, run `docker compose build gateway` to pick up the change (no
rebuild needed for `GLM_Episode_Prompt.txt`, since that one is generated
fresh each time by `generate_episode_prompt.py` and written straight into
the running container's `outputs\` mount — see below).

**On-disk project output**, mounted at `${PROJECTS_BASE_PATH_HOST}` from
`.env` (e.g. `D:\AI_Movies`), created by `load_story_to_db.py`:
```
D:\AI_Movies\PROJECT_ID\
├── project.json
├── story\{story_bible,characters,locations,wardrobe,props}.json
├── episodes\EP_001\
│   ├── episode.json
│   ├── scenes\EP_001_SC_01.json ...
│   ├── image_prompts\EP_001_SC_01_SH_01.txt ...
│   └── video_prompts\EP_001_SC_01_SH_01.txt ...
├── assets\{characters,locations,props}\   (ComfyUI output lands here later)
└── final\                                  (assembled episode output lands here later)
```

## Prerequisite: verify GPU passthrough works

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```
Should print your RTX 5080's stats. If it errors, fix Docker Desktop's
WSL2 GPU support before continuing — otherwise the containerized Ollama
will silently run on CPU and be unusably slow.

## First-time setup (in order)

**1. Stop native Windows Ollama** (system tray → quit, or stop the service)
so the container can safely reuse its model files without a write conflict.

**2. Configure environment** (from `db\`):
```powershell
cd db
copy .env.example .env
notepad .env
```
Set real DB passwords, your actual `OLLAMA_MODELS_PATH` (your Windows
`.ollama` folder — this is what avoids re-downloading `glm-4.7-flash`),
and `PROJECTS_BASE_PATH_HOST`.

**3. Build and start everything:**
```powershell
docker compose up -d --build
docker compose ps
```
First boot: MySQL runs everything in `init\` automatically. The gateway
container builds from `gateway\Dockerfile` and then just idles, waiting
for you to run commands into it.

**4. Create the GLM model inside the container** (only needed once, or
whenever the root `Modelfile` changes — it's mounted directly into the
container now, so there's nothing to keep in sync):
```powershell
docker compose exec ollama ollama create glm-film-director -f /modelfile/Modelfile
```

**5. Open phpMyAdmin** at **http://localhost:8081** to confirm the database
is reachable (log in with your `.env` credentials).

## Everyday use — every command below is `docker compose exec gateway ...`

**Test the model responds:**
```powershell
docker compose exec gateway python run_glm_prompt.py GLM_Test_Prompt_Small.txt
```

**Generate a project's full bible + first episode:**
```powershell
docker compose exec gateway python run_glm_prompt.py GLM_Test_Prompt.txt
docker compose exec gateway python load_story_to_db.py outputs/GLM_Test_Output.json
```

**Generate the next episode of an existing project:**
```powershell
docker compose exec gateway python generate_episode_prompt.py PROJECT_ID EPISODE_NUMBER MINUTES
docker compose exec gateway python run_glm_prompt.py GLM_Episode_Prompt.txt
docker compose exec gateway python load_story_to_db.py outputs/GLM_Test_Output.json PROJECT_ID
```
Example — a 2-minute episode 2 of `ECHO_ENGINE_01`:
```powershell
docker compose exec gateway python generate_episode_prompt.py ECHO_ENGINE_01 2 2
docker compose exec gateway python run_glm_prompt.py GLM_Episode_Prompt.txt
docker compose exec gateway python load_story_to_db.py outputs/GLM_Test_Output.json ECHO_ENGINE_01
```
Repeat with the next episode number for as many episodes as the series needs.

**Watch it happen from Windows without opening a container shell:**
- Raw JSON responses: `db\gateway\outputs\GLM_Test_Output.json`
- Project folders/prompts: wherever `PROJECTS_BASE_PATH_HOST` points (e.g. `D:\AI_Movies\...`)
- Database contents: http://localhost:8081

## Re-running commands safely

- **Episode generation is safe to re-run** for the same episode number —
  `ON DUPLICATE KEY UPDATE` overwrites that episode's rows (and its
  continuity snapshot) instead of duplicating them.
- **The full-bible prompt (`GLM_Test_Prompt.txt`) is NOT guaranteed
  idempotent.** It doesn't pin a `project_id` — GLM invents one from the
  concept text each time, and isn't guaranteed to pick the same one twice.
  Re-running it can create a second, separate project instead of updating
  the first. Treat it as a one-time bootstrap per new story concept, not
  something to re-run for an existing project.
- **Entity IDs (`character_id`, `location_id`, `episode_id`, etc.) are
  automatically scoped to their project** before hitting the database
  (e.g. `CHAR_001` becomes `ECHO_ENGINE_01__CHAR_001`), so two different
  projects generating the same raw ID — which GLM will do routinely,
  since it numbers fresh from 001 every time — can never collide or
  overwrite each other. This happens transparently; you never need to
  type the scoped form yourself. Your on-disk filenames stay short and
  unscoped, since that collision risk only exists in the shared database.
- **Passing the wrong `PROJECT_ID`** to `load_story_to_db.py` will still
  succeed and attach that episode to the wrong project's episode list —
  the scoping fix prevents data *corruption* across projects, but doesn't
  catch a simple wrong-argument mistake. Double check the ID before running.

**Applying `03_fix_constraints.sql` to an already-running database**
(needed once, since MySQL only auto-runs `init/` files on first container
creation):
```powershell
docker exec -i glm_mysql mysql -u root -p glm_pipeline < db\init\03_fix_constraints.sql
```

## Fresh start, switching to Qwen as the primary model

This wipes everything (database + generated project files) and reconfigures
the pipeline so Qwen is what every command uses by default, instead of GLM.

**Your host-native Ollama installation and the `film-director-qwen` model
are untouched by any of this** — they live outside Docker entirely, so
`docker compose down -v` never affects them. You do NOT need to re-run
`ollama create film-director-qwen -f Modelfile.qwen` unless you've also
separately reset your host's Ollama data.

**1. Wipe the database:**
```powershell
docker compose down -v
```

**2. Wipe generated project files:**
```powershell
Remove-Item -Recurse -Force "D:\AI_Movies\*"
Remove-Item -Recurse -Force "D:\AI\GLM-Firms\db\gateway\outputs\*"
```

**3. Set Qwen as the default in `.env`** — open `db\.env` and change:
```
MODEL_NAME=film-director-qwen
OLLAMA_HOST=host.docker.internal:11434
```
Leave every other line (`MYSQL_*`, `OLLAMA_MODELS_PATH`, `PROJECTS_BASE_PATH_HOST`) as they already are.

**4. Start only what Qwen actually needs.** Since Qwen runs on your host's
native Ollama, not the containerized one, you don't need the `ollama`
container running at all for this — skipping it avoids two separate
processes competing for your RTX 5080's 16GB VRAM at once:
```powershell
docker compose up -d --build mysql phpmyadmin gateway
```
(If you want the containerized GLM setup available again later too, run
`docker compose up -d ollama` separately at that point — just not at the
same time you're actively generating with Qwen, to avoid GPU contention.)

**5. Confirm Qwen responds correctly:**
```powershell
docker compose exec gateway python run_glm_prompt.py GLM_Test_Prompt_Small.txt
```
Should complete with `Schema check passed` and a `project` object in the output.

**6. Generate your first real project with Qwen:**
```powershell
docker compose exec gateway python run_glm_prompt.py GLM_Test_Prompt.txt
docker compose exec gateway python load_story_to_db.py outputs/GLM_Test_Output.json
```
Note the `project_id` it reports — that's what you'll use for every
`generate_episode_prompt.py` / `run_episode_batch.py` call from here on.



`Run_GLM_Test.ps1`, `Run_GLM_Test_Streaming.ps1`, and running Python/Ollama
directly on Windows are superseded by the gateway container. Keep the
`.ps1` files around only if you want a quick host-side fallback for
debugging — they still work against the container's Ollama by pointing
at `http://localhost:11434` (since that port is still published), but
they are not part of the normal workflow anymore.

## Stopping / resetting

```powershell
docker compose stop        # stop all containers, keep all data
docker compose down        # stop and remove containers, keep the mysql data volume
docker compose down -v     # WARNING: also deletes the MySQL data volume — full reset
```
Your Ollama models and `D:\AI_Movies` project files are never deleted by
any of the above — they live outside Docker's managed volumes, on your
own filesystem/folder mounts.
