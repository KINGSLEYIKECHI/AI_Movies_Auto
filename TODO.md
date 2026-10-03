# DuPac Film Studio — living project TODO

Built by **Nexaibyte Ltd**. Last reviewed: **3 October 2026**.

## Goal and agreed behavior

Start with a movie concept, episode count, scenes, shots and episode runtime; produce editable story data, references, shot images, videos and exports. Automate the production work while preserving the user's control and review at each stage. Support ComfyUI LTX and MiniMax H3. MiniMax uses 10-second shots; switching back to LTX restores original planned timings. Keep model duration restrictions intact.

Checked items below mean **implemented in source**, not necessarily verified on the build machine. Open validation tasks track that distinction. Proposed tasks are options for future work, not permission to submit renders, spend API credits or delete existing media automatically.

## Current review

- [x] Add a living TODO and reusable update/build/commit instructions.
- [x] Implement confirmed deletion of video versions, related local files and unused historical jobs; protect dependent/shared media.
- [x] Journal file cleanup so failed database transactions restore files and pending cleanup can be retried.
- [x] Recover verified ready videos without duplicate rendering; expose a manual check and configurable recovery during enabled automation.
- [x] Expose video prompt-rule toggles and editable additional production instructions; retain current defaults.
- [x] Add Nexaibyte Ltd credit and improve checkbox layout.
- [ ] **P0 — Validate the latest maintenance update on the build machine.** Delete a disposable unused version, verify its files and job disappear, verify another version stays available, and confirm a referenced version is protected.
- [ ] **P0 — Verify real recovery after a gateway restart.** Completed clips should return to Review; unknown submissions must remain unchanged, with no duplicate ComfyUI prompt.
- [ ] **P0 — Verify prompt toggles on real renders.** Saved rules must survive refresh and appear correctly in the exact submitted prompt.

Latest maintenance checks: 121 Python tests with mocked database/providers, six existing render UI checks, and a browser fixture test of saved prompt toggles. Live Docker/MySQL/GPU behavior still needs the checks above. No push or deployment is implied by these results.

## Implemented foundation

- [x] Interactive movie planning, editable prompts and staged production/review.
- [x] Automatic reference, shot-image and video batches with review gates.
- [x] Approved character/location/prop references selected for the appropriate shot; optional manual reference inputs.
- [x] Accept/reject and create replacement versions of accepted or rejected assets.
- [x] Confirmed project deletion and scoped cleanup.
- [x] Saved ComfyUI submission tracking, interrupted-job inspection and explicit lost-job repair.
- [x] LTX/MiniMax engine switching with preserved original LTX timing.
- [x] Dialogue assignments, sequential speaker windows by default, explicit simultaneous delivery and voiceover options.
- [x] Configurable ending holds; measured separate speech must fit before the hold without truncation.
- [x] Separate dialogue/effects/ambience inputs, optional speech generation and transcript checks, continuous episode music.
- [x] Previous-shot ending-frame continuity within a scene and recorded reference lineage.
- [x] Video quality reporting, human completion confirmation and strict export timing checks.

Native video prompts guide speech and lip-sync; they do not prove accurate speaker identity, completed action or mouth movement. Separate on-screen speech requires a compatible installed lip-sync backend. These limits remain open validation/integration work below.

## Next priorities

### P1 — LTX Director and production navigation

- [ ] Receive the user's **API workflow export, node definitions and a successful example** for LTX Director. Dependency: user-provided workflow; do not guess nodes or bindings.
- [ ] Map Director controls into editable shot settings and computed prompts; validate required files, nodes and supported engine durations before enabling them.
- [ ] Keep ordinary LTX/MiniMax rendering available if Director is disabled or unavailable.
- [ ] Add a clear scene → shot → asset navigation view with stage progress, next-action guidance and links to the relevant editor/review screen.
- [ ] Add a shot timeline showing selected version, planned/actual duration, dialogue, transitions and continuity source.

### P1 — Reliability and transparent control

- [ ] Add structured failure categories and actionable messages for missing models, invalid workflows, memory pressure, provider outages and interrupted postprocessing.
- [ ] Expand preflight: workflow/node/model availability, output permissions, free disk space and provider access; show readiness before submitting work.
- [ ] Persist worker ownership/heartbeats and recovery decisions so multiple controllers cannot claim the same job.
- [ ] Add bounded retries with backoff **only for classified safe transient operations**. Unknown submission outcomes must be checked before any resubmission.
- [ ] Record recovery/cleanup events in a visible activity log with the reason and result.
- [ ] Audit remaining planner, image and assistant prompt templates: make creative instructions editable/conditional where useful; preserve schema, IDs, model limits and file safety requirements.
- [ ] Provide a prompt preview/diff before saving changes and record which rules produced each version.

### P1 — Dialogue, audio and continuity quality

- [ ] Validate a real multi-character lip-sync backend using separate voices and per-speaker timing; prevent a listener from mouthing another character's line.
- [ ] Add speech-specific timing/alignment checks to detect dialogue continuing into the ending hold. Do not mistake foreground effects for speech.
- [ ] Align subtitles to measured speech rather than equal estimated intervals.
- [ ] Add per-line delivery controls, pauses and pronunciation guidance with timing validation.
- [ ] Test continuity across action, wardrobe, props, lighting, camera direction and completed ending states on a complete episode.
- [ ] Add an editable continuity library and rules for intentional scene/angle changes.

### P2 — Review, storage and efficient regeneration

- [ ] Show a dependency graph and preview affected descendants before regenerating or deleting media.
- [ ] Compare video versions side by side; capture rejection reasons and use them in an editable proposed replacement prompt.
- [ ] Pin the exact version used in each episode cut and show whether an export is stale.
- [ ] Add a storage usage view and preview unused versions for optional batch cleanup; confirmation remains required.
- [ ] Add restorable trash/archive and project export/import bundles with settings, story, prompts, media and reference history.
- [ ] Offer explicit ComfyUI output cleanup after verifying ownership; current deletion cleans pipeline-local copies only.

### P2 — Budgets and model resources

- [ ] Preview render count, estimated time and configured provider costs before production.
- [ ] Add configurable spending/time limits, actual usage reporting and a clear pause when limits are reached.
- [ ] Coordinate Ollama/ComfyUI memory ownership and expose unload results and failures.
- [ ] Benchmark supported workflows and resource settings on the build machine; use those results for conditional recommendations.

## How to keep this list current

For every feature or fix: review related items, update implementation/validation status, record remaining limitations, and add newly discovered work under the appropriate priority. Preserve unresolved tasks across releases. Split large tasks into concrete deliverables. Mark blocked tasks with the missing input. Record the date and relevant test evidence without treating mocked tests as live media verification.

Use [UPDATE_WORKFLOW.md](UPDATE_WORKFLOW.md) for the required build commands, Git commit messages and delivery checklist for every update. Existing operating instructions remain in [STUDIO_GUIDE.md](STUDIO_GUIDE.md).
