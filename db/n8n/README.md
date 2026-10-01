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
Import `film-automation-schedule.json` for automatic continuation of the existing
planned story. It submits a one-job pipeline batch every five minutes and stops
production at approval gates, rejection, or failures. Set its project URL before
activating for another story. Activating permits paid OpenAI image generation.
It is inactive by default. Busy-worker responses use the error output; no second
render starts. Use run logs and gateway status to inspect failures. New-story
planning remains controlled by the existing Ollama scripts.
