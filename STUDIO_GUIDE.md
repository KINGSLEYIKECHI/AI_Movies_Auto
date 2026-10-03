# DuPac Film Studio

## Dialogue, audio and continuity

In **Production**, expand **Dialogue, sound & shot continuity**. The default
enables dialogue and foreground sound effects, disables ambient sound and
music, and leaves optional paid speech services off. Save the settings before
rendering replacement video versions. Existing media are preserved.

Select a shot to edit its exact spoken lines, character IDs, starting state,
completed ending state, and effects description. **Draft dialogue & ending
with the local model** creates an editable suggestion; it does not save until
you choose **Save shot dialogue & ending state**. New movie planning also asks
for achievable actions, dialogue and settled endings. Silent shots stay silent
unless dialogue is supplied; existing stories are not rewritten automatically.

The renderer reads `shot_dialogue` and includes speaker names, stable speaker
IDs, language and exact lines in its computed instructions. It rejects speech
that is too long for the available shot time. The final second is reserved for
settling after action and speech. The model still needs human review to verify
that the action actually finishes and the lips match.

Continuity modes:

- **Continuous action** extracts a frame near the preceding video's end and
  uses it as the next clip's starting image. Only shots in the same scene are
  linked. By default a preceding candidate or accepted video can be used; turn
  on accepted-only continuity to require review before the next clip.
- **New angle** uses the approved planned shot image and preceding shot prompt
  and saved ending-state context. It does not automatically redraw the planned
  image; revise that image if it contradicts the preceding ending state.
- **New scene** uses the planned image without carrying the prior scene frame.
- An explicitly uploaded starting frame takes precedence over the automatic
  continuity frame. Previous-frame asset lineage is saved on the video job.

Native audio uses model prompting, so ambience/music removal and identical
voices are not guaranteed. **Separate audio** discards the entire generated
mixed track and combines only enabled uploaded stems and optional synthesised
dialogue. Effects must be supplied separately; the pipeline refuses to silently
discard required effects. Audio uploads accept WAV, MP3, M4A, FLAC and OGG up to
20 MB, scoped to the selected project.

Optional OpenAI speech requires `OPENAI_API_KEY`, explicit speech-provider
selection and per-character voice choices. It incurs API usage charges and
uses AI-generated voices. `SPEECH_MODEL` defaults to `gpt-4o-mini-tts`; speech
is cached by text, language, model and voice. Generated dialogue that does not
fit is rejected rather than cut short. Optional transcript checks use
`SPEECH_CHECK_MODEL` (default `gpt-4o-mini-transcribe`) and also incur charges.
Word coverage is a review aid, not proof of pronunciation or speaker accuracy.

On-screen separate dialogue requires a separately installed lip-sync backend.
Configure `LIPSYNC_COMMAND_JSON` in `db/.env` as a JSON argument array for a
trusted executable available inside the gateway container. Include `{video}`,
`{audio}` and `{output}` placeholders. The command receives the silent source
video, the completed dialogue stem, and a new output path. It is invoked
without a shell. No lip-sync model is installed by this change. Off-screen
voiceover works without lip-sync. Configure a backend suitable for your scene,
especially for multiple visible speakers.

If music is enabled, upload and select one episode music track. Assembly loops
and mixes that track across the entire episode at a fixed low gain, rather
than asking each video model to invent a separate tune. Dialogue, effects and
ambience stems are selected per shot. The original generated mix is retained
as `.raw.mp4` when separate processing is used.

Each new video receives duration, audio-presence and near-silence checks, plus
optional transcription. Review shows the report and exact submitted prompt.
Acceptance requires confirming that action/dialogue finish before the cut.
Strict exports refuse noticeable duration mismatch or recorded quality
warnings. Disabling strict checks deliberately permits trimming/padding.
Semantic action completion is checked by the reviewer, not a vision model.
Audio/quality-processing failures can retry the saved video without another
ComfyUI submission. Fix the direction settings or missing backend first.

Persistence: dialogue is in MySQL `shot_dialogue`; direction settings are in
project `video_direction.json` and `shot_direction.json`; uploaded stems are
under `audio_inputs`; quality reports are beside each clip as `.quality.json`
and copied into `asset_records.metadata.quality`. The existing project cleanup
removes these project-scoped files as well. No database migration is required.

Rebuild the gateway with both compose files after transferring the updated
source, then reload the Studio with Ctrl+Shift+R. Test one replacement clip
before enabling full automatic production. Native models, paid speech calls
and installed lip-sync need validation on the build machine.

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

Existing projects can now use **Approve prompts & start production** without a
new studio-created story plan. The gateway saves review/automation controls while
retaining their original shot durations, model locks and story data. Review each
stage remains the default.

Queued/failed prompts save normally, including when the text is unchanged.
Selecting a completed prompt enables editing with **Queue edited version** rather
than overwriting the prompt that produced its original media. Use **Edit image /
video version** to add image references or modify the original image.

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

## Resume after the unread-result queue error

The queue existence check now selects at most one matching job, and queue/pipeline
cursors are buffered. Multiple asset versions no longer leave unread result rows.
No project reset or database migration is required for this fix.

Transfer the updated source to the build machine, then run from `db` while no
worker is active:

```powershell
docker compose -f docker-compose.yml -f docker-compose.automation.yml up -d --build --force-recreate gateway
Invoke-RestMethod http://localhost:8000/health
```

Reload http://localhost:8000/review with Ctrl+Shift+R and select the existing project.
In **Prompts**, save edits or queue replacement versions as needed. Choose **Approve
prompts & start production** to let automation finish the remaining reference
renders, then pause for review before producing shot images and videos.

For manual work, pause automatic production and use **Generate the remaining
assets** in **Production**:

1. Set the jobs per batch and choose **Generate reference batch**. Repeat as
   needed, then accept the references in **Review**.
2. Choose **Generate shot image batch**. Required character/location references
   must already be accepted. Repeat and review the frames.
3. Choose **Queue missing videos**, then **Generate video batch** after all shot
   images are accepted. Review clips, then assemble from **Exports**.

**Queue missing** only adds missing jobs; **Generate** renders queued jobs. Completed
assets are revised through **Edit / new version**, not through the queue buttons.
If a render job itself is failed, use its retry control after inspecting its log.
The unread-result exception occurs during queuing and does not require deleting
completed assets or resetting all jobs.

## GPU memory without a visible run

**Local engines and GPU memory** shows gateway activity, the configured ComfyUI
queue, and models currently loaded in the configured Ollama server. A loaded
model can retain VRAM while GPU utilisation is zero. It does not prove a render
is active. Other applications or a separate native Ollama instance are not tracked
by this panel.

Use **Release idle model memory** to request Ollama model unloading and ComfyUI
cache/model release. It refuses while a gateway worker, running job record or
ComfyUI queue is active, and refuses if the ComfyUI queue cannot be checked.
ComfyUI releases asynchronously; refresh the activity view after a moment.
These operations do not cancel renders or clear their queues. Models reload for
the next local task.

## Old running videos and new video versions

Saving **MiniMax H3** sets every planned shot to 10 seconds and updates the
production runtime budget and duration lock. The first switch saves original
LTX shot durations and episode runtime in production settings. Saving **LTX 2.5**
restores those timings. Repeated MiniMax saves preserve the original snapshot.
Model-registry duration restrictions remain unchanged and are checked against
the requested timings. The change pauses automation and preserves video files;
regenerate clips to obtain new motion at the selected duration. Existing clips
are padded or trimmed to the active shot timing during assembly.

Older projects do not need to be recreated or adopted before switching. The
gateway creates minimal legacy production settings when missing, and captures
each original shot duration from MySQL. Existing minimal legacy settings are
also supported. Mixed original durations are restored and respected by the
video worker. If a switch fails, database updates are rolled back and the prior
settings file is restored.

Persistence locations:

- MySQL `project_model_config.video_model_key`: current video engine.
- MySQL `project_model_config.locked_duration_seconds`: active duration lock
  (10 for MiniMax; original lock for LTX).
- MySQL `shots.duration_seconds`: active duration for each project shot, scoped
  through `scene_plan.project_id`.
- Project `production.json`: `ltx_original_timing` stores original per-shot
  timings, duration lock and, for newer projects, the original episode budget.
  `spec.video_model_key` mirrors the selection and `automation_enabled` is false
  after switching. This file is persisted under `PROJECTS_BASE_PATH`.
- `model_registry.valid_durations` holds model restrictions and is not altered
  by switching.

After an interrupted ComfyUI session, restart ComfyUI and verify its queue is
empty. In **Production**, choose **Resolve old video jobs** and confirm after
checking ComfyUI's outputs. This pauses automation and checks saved submissions.
Registered completed clips are preserved and their jobs marked done. Stale
records without recoverable results become failed, so **Retry** is available.
Submission records are archived; existing files are kept. This action submits
no videos and refuses while ComfyUI has queued or running work.

For any completed video in **Review**, choose **Edit / regenerate video**.
Accepted, rejected and candidate clips can all create another version. Edit
the prompt and optionally upload one starting frame, then queue or render the
replacement. The original clip and its review status are preserved. The new
clip returns to Review for acceptance or rejection. The selected project video
engine is used; this regenerates from a starting image, rather than editing the
existing video directly.

The same interrupted-job check is available from PowerShell after updating and
rebuilding the gateway:

```powershell
$base = 'http://localhost:8000/projects/nigerian_mechanic_past_machine'
Invoke-RestMethod -Method Post "$base/resolve-video-jobs" -ContentType 'application/json' -Body '{"confirm":true}' | ConvertTo-Json -Depth 6
```
## Speaking turns and ending holds

In Production → Dialogue, sound & shot continuity, save the ending hold and each shot's assigned dialogue. Speakers take turns by default, deliver only their own exact lines once, and remain visibly on screen while speaking. Use **Allow simultaneous dialogue** only for deliberate overlapping delivery. **Off-screen voiceover** remains an explicit exception.

A 10-second shot with a 2-second ending hold has an 8-second speaking window. The hold does not extend the video. Computed prompts show each line's estimated window and require all speech and the main action to finish before the hold. If dialogue cannot fit, shorten it or use a supported longer shot; the pipeline does not cut speech to make it fit.

Separate generated speech uses measured line lengths and pauses between speakers. Uploaded dialogue tracks must fit entirely before the hold, including trailing silence. Effects can continue through the hold. Native LTX/MiniMax audio receives these instructions, but model adherence, speaker identity and visual lip synchronization still require review. Transcription checks wording, not lip movement. Check each speaker, complete sentence and silent hold before accepting the clip.

Changes apply to newly rendered versions. Use **Edit / new version** to regenerate existing clips with the new instructions. Existing approved files are preserved. No database migration is needed: global settings stay in `video_direction.json`, per-shot options in `shot_direction.json`, and dialogue in `shot_dialogue`. Generated separate speech also saves a `.dialogue_timing.json` manifest next to the video; a compatible lip-sync command may optionally accept a `{timing}` placeholder for per-speaker start/end times. Such a backend must actually support multiple visible speakers; the manifest alone does not supply that capability.
