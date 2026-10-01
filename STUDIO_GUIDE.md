# DuPac Film Studio

The studio starts a new production from your movie idea. Open
http://localhost:8000/review after starting the automation stack.

## Update the build machine

Pull the updated source into your existing checkout, then run from its `db` folder:

```powershell
docker compose -f docker-compose.yml -f docker-compose.automation.yml up -d --build
Invoke-RestMethod http://localhost:8000/health
```

Keep your existing `.env`, database volumes and output paths. If migrations 12
and 13 are already applied, this interface update requires no new SQL migration.
For the first automation installation, follow [AUTOMATION_SETUP.md](AUTOMATION_SETUP.md).
The new planner reads the mounted root Modelfile directly on each model call.

## Create a production

1. In **Describe**, enter the title, movie concept, episodes, scenes per episode,
   shots per scene and minutes per episode. Add style and directing instructions.
2. Choose your registered image model and either LTX 2.5 or MiniMax for video.
   Leave LoRA disabled unless you have a compatible installed model. The custom
   list comes from ComfyUI; matching architecture is still your responsibility.
3. Compute the planning prompt. Edit it directly or request a local suggestion,
   inspect the suggestion, and apply it if useful. Changing the production
   settings requires computing the preview again.
4. Generate the production plan. The local director creates the story bible,
   episode outlines, scene plans and shot plans, then queues reference/image jobs.
   Interrupted planning resumes from saved stage checkpoints.
5. In **Prompts**, inspect and edit the queued image prompts and planned video
   prompts. Approve these prompts before starting automatic production.
6. In **Production**, start automation. The gateway checks every ten seconds and
   runs one batch at a time. It continues when the browser is closed.
7. In **Review**, approve or reject the completed candidates. The default pauses
   after canonical references, shot images, video clips and final exports. A stage
   finishes its queued renders before waiting for review. Approval lets automation
   continue; failed jobs and rejected assets stop it until resolved.
8. In **Exports**, inspect the assembled episode files and subtitles.

Use **Pause after batch** before editing prompts. An active batch finishes before
the pause takes effect. Queuing a replacement for a rejected asset also pauses
automation and requires reviewing the replacement prompt before restarting.

## Runtime and saved files

Runtime is divided across the requested shots using whole seconds. Each shot
must receive 2–20 seconds; adjust the count or runtime if the preview rejects the
combination. Assembly trims or pads clips to their planned durations.

Story data and jobs live in MySQL. Production controls and checkpoints are under
`<PROJECTS_BASE_PATH_HOST>/<project_id>/production.json` and `planning/`.
Each planning stage saves its prompt and response. Render workers save media and
provider submission metadata under the same project output folder.

## What to test on the build machine

Start with one episode, one scene and two shots, with a short runtime. Check the
computed prompt and plan, then approve each stage only after viewing its output.
Confirm ComfyUI has the supplied workflow's models and custom nodes, and that
the image model/key configured in your registry is available to your account.
Image generation can incur provider charges once production starts.

Automated code and browser checks use mocks; actual MySQL, provider generation,
ComfyUI GPU rendering and FFmpeg exports still need this machine-level test.
Separate wardrobe rendering, scene-specific prop references, previous-frame
continuity, independent voice/music generation and precise subtitle alignment
are not implemented. Workflow-generated audio is preserved.

n8n is optional for continuing production: the gateway already provides the
timer. The schedule workflow calls `/automation/tick` and respects each project's
enabled state and review gates. Keep it inactive unless you need that integration.

## Edit accepted or rejected assets

Open **Review**, select the asset's status, and choose **Edit / new version**.
Acceptance chooses a version for use; it does not lock editing. You can also accept
a previously rejected version or restore a previous approved version. Accepting a
replacement moves the previous chosen version to **Previous approved versions**.
The media files and generation prompts are preserved.

Edit the prompt and choose whether to **Modify the original image**. That option
uses OpenAI's image-edit endpoint; disabling it generates from the prompt and any
selected references. Upload optional PNG, JPEG or WebP images (10 MB maximum;
25 megapixels maximum) with an identity, style or composition purpose. Select up
to three references for images. Model output can approximate a reference, so
inspect it rather than expecting an exact copy.

Choose **Queue edited version** to inspect it before rendering, or **Queue & render
this version** to render that specific job immediately. The latter can incur an
image API charge. Editing pauses automatic production and requires fresh prompt
approval before automatic work resumes. Changes to a chosen reference return
already-approved downstream media for review; they do not silently rerender it.

For older rejected assets, the API can reconstruct a missing job link from the
asset's entity. If its original image file is missing, disable modifying the original
and regenerate from the prompt. Legacy shot assets still require a matching shot
plan. Inspect the returned error and generation log if rendering fails.

Before the first render, select a queued image prompt in **Prompts**, then choose
**Reference images & render inputs** to attach uploads or select an OpenAI image
model for that job. **Render this queued job** renders the selected job directly.

For video, edit its action prompt and optionally select one uploaded starting
frame. The local engine generates a new video. GPT edits apply to still images;
edit and accept a shot frame first when you want to change a video's visual design.
Final exports use **Assemble another cut** and preserve previous export files.
The local Flux text-to-image fallback refuses jobs requesting OpenAI image edits,
so it cannot silently discard your uploaded references.

## Delete a production

Choose **Delete production** beside the project selector. The confirmation names
the project, shows its job/asset counts and output folder, and offers **Keep
production** or **Yes, delete production**. No deletion happens until confirmation.

Deletion removes project-owned MySQL rows (including shots without a project FK),
asset-reference links, uploads, render options, media, production settings, planning
checkpoints and matching gateway run records/logs. Shared models, database volumes
and other projects are kept. Deletion is blocked by active workers, unresolved
running jobs, links from other projects, or file paths outside the project folder.

The project folder is staged under `<PROJECTS_BASE_PATH_HOST>/.deleting/` before
the database commit. Database failure restores it. If file cleanup fails after the
commit, the confirmation offers **Retry file cleanup**; do not treat that partial
result as complete. Retrying also reconciles interrupted cleanup journals.

Cleanup covers the pipeline's managed database, output folder and gateway logs.
Separate backups, manually copied files and ComfyUI's own server-side history/cache
are outside this deletion boundary. No new SQL migration is needed for these
controls; rebuild the gateway to install the upload-validation dependency.
