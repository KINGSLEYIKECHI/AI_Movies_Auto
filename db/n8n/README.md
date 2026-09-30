# n8n workflows

Import `film-automation-review.json` from the n8n editor and replace
`PROJECT_ID` in the HTTP Request node with the project being produced. The
gateway automation API is reached as `http://gateway:8000` from within the
n8n container; it is not published on the host.

The API routes are:

- `GET /projects/{project_id}/review-assets?status=candidate`
- `POST /projects/{project_id}/assets/{asset_id}/review` with JSON
  `{ "status": "approved" }` or `{ "status": "rejected" }`
- `POST /projects/{project_id}/workers/openai-references`
- `POST /projects/{project_id}/workers/comfy-references`

Use the list endpoint to create the review page, then wire an approval button
or form to the review endpoint. Trigger a worker only after the relevant
approved assets exist.
