# Change delivery and build workflow

Every change report should contain:

1. What changed and where to use it.
2. Related TODO items completed, awaiting live validation, blocked or newly added.
3. Checks run and their limits, including whether Docker/GPU/provider testing occurred.
4. Exact update/build commands and any required migration/configuration steps. Say explicitly when none are needed.
5. A suggested Git commit message and clear committed/pushed/deployed status.

Review [TODO.md](TODO.md) before starting and again before delivery. Keep it current as the project develops. Do not mark future proposals implemented.

## Development machine: inspect, commit and push

Run from the repository root. Review the changes before staging. Stage only files belonging to the intended update; do not add `.env`, credentials, generated media or unrelated work.

For the roadmap/documentation update:

```powershell
Set-Location "D:\DuPac Studio Project\Version1\movie_auto"
git status --short
git diff
git add -- TODO.md UPDATE_WORKFLOW.md AGENTS.md PIPELINE_IMPROVEMENTS.md
git commit -m "docs: add living production roadmap and update delivery workflow"
git push
```

The preceding video maintenance implementation is a separate pending change. Suggested commit message:

```text
feat: add safe video cleanup and configurable production recovery

Add confirmed video-version deletion with dependency protection and cleanup recovery.
Expose prompt-rule toggles, editable instructions and verified video recovery.
Improve checkbox layout and credit Nexaibyte Ltd.
```

Stage that implementation separately when ready:

```powershell
git add -- STUDIO_GUIDE.md db/gateway/automation_api.py db/gateway/project_cleanup.py db/gateway/review.html db/gateway/studio.css db/gateway/studio.js db/gateway/video_direction.py db/gateway/tests/test_video_maintenance.py
git commit -m "feat: add safe video cleanup and configurable production recovery"
git push
```

These commands are instructions; their inclusion does not mean a commit or push has been performed. If the repository uses a different branch or requires a PR, follow that repository's workflow. Resolve pull conflicts before rebuilding.

## Build machine: pull, rebuild and inspect

Wait for active gateway work to finish before recreating it. Run:

```powershell
Set-Location "D:\AI\GLM-Firms-v2\AI_Movies_Auto"
git pull
Set-Location ".\db"
docker compose -f docker-compose.yml -f docker-compose.automation.yml up -d --build --force-recreate gateway
docker compose -f docker-compose.yml -f docker-compose.automation.yml ps gateway
Invoke-RestMethod "http://localhost:8000/health"
docker compose -f docker-compose.yml -f docker-compose.automation.yml logs --tail 100 gateway
```

Open `http://localhost:8000/review` and refresh with **Ctrl + F5**. ComfyUI must be running separately for local video generation; rebuilding the gateway does not start the Windows ComfyUI process.

Run the Compose command from `db`, where both Compose files are saved. The maintenance and roadmap updates require **no database migration**. A documentation-only update does not require a rebuild, but the command above is the standard rebuild command when applying pending gateway changes too.

For future changes that require another service, a database migration or new environment values, provide the exact additional steps in that update's delivery report. Do not use a database-volume reset as an upgrade procedure.

## Review after applying an update

- Check gateway health and the page's current production status.
- Use disposable media for deletion/cleanup checks.
- Verify saved options survive refresh and affect the computed/submitted prompts.
- Check the updated behavior without duplicating an unresolved render.
- Record the result against the relevant open validation task in `TODO.md`.
