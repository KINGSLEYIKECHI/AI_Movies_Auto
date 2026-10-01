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
