# n8n production controls

Import `film-automation-control.json` in n8n, then publish/activate it. The
older `film-automation-review.json` remains a manual candidate-list example.
The gateway must be healthy first; n8n reaches it at `http://gateway:8000`.
Browser previews must use `http://localhost:8000`, not the Docker hostname.

The control workflow provides these production webhooks:

| Method | n8n path | Input |
| --- | --- | --- |
| POST | `/webhook/film/queue` | JSON `project_id`, `stage`: references, shot-images, shot-videos |
| POST | `/webhook/film/worker` | JSON `project_id`, `worker`, `limit` (defaults to 1), optional `episode_id` |
| GET | `/webhook/film/run?run_id=...` | Poll the returned run ID |
| GET | `/webhook/film/assets?project_id=...` | List candidate assets |
| POST | `/webhook/film/review` | JSON `project_id`, `asset_id`, `status`: approved or rejected |

Worker names: `openai-references`, `comfy-references`, `comfy-videos`, `assembly`, `pipeline`.
Assembly requires `episode_id`. Worker webhooks start a batch and return promptly;
poll the run endpoint until it is done/failed/interrupted before advancing.
The visual interface at http://localhost:8000/review uses the same gateway API.

Example from PowerShell (after activating the workflow):

```powershell
$payload = @{ project_id = 'nigerian_mechanic_past_machine'; stage = 'references' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:5678/webhook/film/queue -ContentType application/json -Body $payload
$payload = @{ project_id = 'nigerian_mechanic_past_machine'; worker = 'openai-references'; limit = 1 } | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri http://localhost:5678/webhook/film/worker -ContentType application/json -Body $payload
Invoke-RestMethod "http://localhost:5678/webhook/film/run?run_id=$($run.id)"
```

During editor testing, use `/webhook-test/` URLs while the matching webhook is
listening. No credentials/API keys belong in the workflow JSON. These local
controls assume one trusted operator; Compose publishes them only on loopback.
Import `film-automation-schedule.json` only if you need n8n continuation.
It calls `/automation/tick` every five minutes for productions enabled in the studio
and respects prompt approval and stage reviews. The gateway already runs its own
ten-second timer. The workflow is inactive by default; enabled production permits
paid image generation. New productions are planned in the studio interface.


### Automatic production and manual image references

In the Studio, approve prompts and start automatic production once. The scheduler processes the queued assets in order and pauses for your selected stage reviews. With “Review each stage,” accept the canonical references in Review, then automation continues through shot images, videos and exports. You do not need to render each queued image manually.

Each shot image automatically receives the accepted images for the characters in its shot plan and the location in its scene plan. Missing accepted references keep the shot queued. The OpenAI image worker sends image numbers, character names, entity IDs and location roles with the prompt to preserve context.

For manual changes, select a shot under Prompts and open “Reference images & render inputs.” Planned characters and locations appear as automatically included thumbnails. Select other accepted character, location, prop or shot images for additional context, or upload optional identity/style/composition images. Save render inputs for later automation, or save and render that job. Edit the story/shot plan to change the required cast; extra references do not replace the planned cast. Video generation uses the accepted shot image as its starting frame.

After updating these files on your build machine, run from the `db` folder:

```powershell
docker compose -f docker-compose.yml -f docker-compose.automation.yml up -d --build gateway
```

Open the Studio at `http://localhost:8000/review` and refresh the browser.


### A video finished but production is stuck

Manual batch size applies only to manual generation. Automatic production schedules one job per run and continues until the selected stage review is due.

After updating the gateway, use **Check saved video render** under **Production → Jobs that need attention** for a failed or interrupted video. This checks its saved ComfyUI prompt, downloads the finished clip, creates its review asset and marks the job done without submitting another render. Unknown/missing history remains unresolved rather than triggering duplicate work. A proven ComfyUI error can be retried; its old submission record is archived.

If the gateway itself is still waiting on an old worker, restart only the gateway after checking ComfyUI's queue:

```powershell
docker compose -f docker-compose.yml -f docker-compose.automation.yml restart gateway
```

Restarting the gateway interrupts its waiting worker; ComfyUI continues any already submitted render. Then refresh the Studio and check the saved video. Do not delete the submission sidecar or reset the database job manually.

The video worker requests ComfyUI model unloading after each saved clip when its queue is empty. Failures to unload are logged as warnings and do not undo a finished video or stop the batch. Set `RELEASE_VIDEO_MEMORY=0` in the gateway environment if you later prefer faster sequential renders with models kept loaded. The manual **Release idle model memory** button also requests Ollama unloading. Task Manager can still show GPU memory held by other applications; an unload request is not a guarantee that all GPU memory becomes empty.


For the full worker log, expand **Current run → Generation log** and download the full latest run log. On the build machine it is also saved under `db/gateway/outputs/runs/*.log`. From the `db` folder, read the newest worker log with:

```powershell
Get-ChildItem .\gateway\outputs\runs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content -Tail 150
```

Gateway container logs mainly show API/server activity; video generation errors also appear in the worker log and the ComfyUI console.


### Video recovery after a ComfyUI crash

The LTX workflow now puts its text encoder on CPU by default (`LTX_TEXT_ENCODER_DEVICE=cpu`). This avoids spending GPU memory on the encoder that failed in the supplied log; it uses system RAM and may run slower. To restore the workflow's original placement later, set `LTX_TEXT_ENCODER_DEVICE=default` in the gateway environment. The renderer still needs enough GPU and system memory for the chosen video model.

Video batches stop at their first failure and leave subsequent jobs queued. A new video is submitted only when ComfyUI's queue is empty. During a render, its saved submission record and log show a waiting phase every 30 seconds. Temporary status-check network outages are retried for up to 90 seconds (`COMFYUI_NETWORK_GRACE_SECONDS`), without submitting another prompt. If a saved prompt disappears from queue and history for 30 seconds (`COMFYUI_MISSING_GRACE_SECONDS`), the worker stops and directs you to recovery. Total render timeout remains `COMFYUI_TIMEOUT_SECONDS` (default 7200). A ComfyUI thread crash can still require restarting ComfyUI; the gateway cannot prevent CUDA errors or repair that process.

Completed downloads have an integrity receipt in their saved submission record. If the gateway restarts before committing the database result, recovery can reuse that verified local download even if ComfyUI no longer retains its history.

After restarting ComfyUI, check its queue is empty. Under **Production → Jobs that need attention**, click **Check saved video render** for each unresolved job. If it has no queue entry or history, **Reset lost submission** appears. Inspect ComfyUI's output folder first, then confirm the reset. It archives the old submission, leaves media files intact, queues one replacement and pauses automation. An existing local video file or ComfyUI history/queue work blocks reset. Test one video before restarting automatic production.

For the reported jobs, the API reset command is available only after checking recovery and confirming that ComfyUI's queue is empty and no untracked finished clip exists:

```powershell
$projectApi = 'http://localhost:8000/projects/nigerian_mechanic_past_machine'
$reset81 = @{prompt_id='417661f8-cfff-4053-8ce5-769a06c3fa81'; confirm=$true} | ConvertTo-Json
Invoke-RestMethod -Method Post "$projectApi/jobs/81/reset-lost-video" -ContentType 'application/json' -Body $reset81
$reset82 = @{prompt_id='7e254082-9a9c-4ad5-a3e9-37e9f75a22a8'; confirm=$true} | ConvertTo-Json
Invoke-RestMethod -Method Post "$projectApi/jobs/82/reset-lost-video" -ContentType 'application/json' -Body $reset82
```

These commands apply only to those saved submissions. For other jobs, use the prompt ID returned by **Check saved video render**. Submission POST timeouts with no known prompt ID remain blocked because their outcome is ambiguous; inspect the saved client ID against ComfyUI queue/history before any replacement.


### Choose LTX 2.5 or MiniMax H3 for an existing production

In **Production → Video engine for this production**, select **LTX 2.5** or **MiniMax H3**, then click **Save video engine**. The chosen engine applies to upcoming video jobs and newly generated versions; existing clips retain their original model. Switching pauses automation, retains the approved shot durations, and resets LoRA to None. Both engines require their registered ComfyUI workflow and installed model files. Models marked unavailable cannot be selected. Incompatible shot durations block the switch.

Resolve running jobs and saved submissions first, and ensure ComfyUI's queue is empty. Test one video with the selected engine, then restart automation. New productions also have this choice under **Describe → Local video engine**.
