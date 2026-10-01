# Further production controls

The current update adds editable versions, optional uploaded image references,
individual rendering and confirmed project deletion. The following work is
recommended next, in order of usefulness.

1. **Dependency-aware regeneration.** Show which shots, clips and exports used
   each reference version. Offer a preview of affected assets, then rerender only
   the selected descendants. Current changes conservatively return downstream
   approved media for review; they do not regenerate it automatically.
2. **Budgets and a generation plan.** Before starting, show the queued render
   count and a configurable spending/render-time limit. Stop when the limit is
   reached and record actual usage per job. Use configured provider pricing rather
   than hardcoded estimates.
3. **Provider and GPU preflight.** Check model access, workflow nodes, checkpoint
   availability, output permissions, free disk space and GPU memory before a run.
   Coordinate Ollama unloading with ComfyUI rendering, and recover submitted jobs
   by provider ID so a restart does not duplicate paid or GPU work.
4. **A shot timeline.** Edit scene order, shot duration, dialogue and transitions,
   preview the episode, and choose exactly which version goes into each cut.
5. **A complete continuity library.** Manage wardrobe, props, character poses and
   location angles alongside character identity; allow approved previous-shot
   frames as optional conditioning references.
6. **Audio and subtitle controls.** Generate/edit dialogue voices, music and sound
   effects on separate tracks, then align subtitles to the actual speech.
7. **Version comparison and selective approval.** Compare two images or clips
   side by side, record rejection reasons and automatically include that feedback
   in a proposed next prompt. Approve a chosen version for a particular shot or cut.
8. **Archive and export.** Export a project bundle with story data, prompt history,
   references, media and settings. Provide restorable archive/trash controls in
   addition to permanent deletion.

These are proposed enhancements, not implemented features. A dependency graph,
budget control and preflight checks would provide the largest immediate increase
in control and reduce wasted rendering.
