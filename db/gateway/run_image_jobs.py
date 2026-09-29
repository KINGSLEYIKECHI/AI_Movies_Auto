r"""
Process queued image jobs (character/location/prop references, and shot
images) for a project, one at a time, using OpenAI's Images API.

This is a backend-specific worker: it only processes jobs whose project
has image_model_key pointing at a model_registry row with backend =
'openai_api'. A future ComfyUI-backed worker would be a separate script,
selected the same way — this is the pluggable-backend design point.

Usage:
    python run_image_jobs.py PROJECT_ID [--retry-failed] [--limit N]

Requires OPENAI_API_KEY in .env.
"""

import base64
import os
import sys
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "glm_user"),
    "password": os.getenv("MYSQL_PASSWORD", "changeme_user"),
    "database": os.getenv("MYSQL_DATABASE", "glm_pipeline"),
}

IMAGE_JOB_TYPES = ("character_reference", "location_reference", "prop_reference", "shot_image")


def fetch_image_backend(cursor, project_id: str):
    cursor.execute(
        "SELECT pmc.image_model_key, mr.backend, mr.available "
        "FROM project_model_config pmc "
        "JOIN model_registry mr ON mr.model_key = pmc.image_model_key "
        "WHERE pmc.project_id = %s",
        (project_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise SystemExit(f"No model config locked for '{project_id}'. Run set_project_models.py first.")
    model_key, backend, available = row
    if backend != "openai_api":
        raise SystemExit(
            f"'{project_id}' is locked to image model '{model_key}' with backend '{backend}', "
            f"not 'openai_api'. This worker only handles the OpenAI backend — use a different "
            f"worker for '{backend}'."
        )
    if not available:
        print(f"WARNING: '{model_key}' is marked unavailable in the registry — proceeding anyway.")
    return model_key


def fetch_next_job(cursor, project_id: str, include_failed: bool):
    statuses = "('queued','failed')" if include_failed else "('queued')"
    cursor.execute(
        f"SELECT id, job_type, prompt, output_path FROM jobs "
        f"WHERE project_id = %s AND job_type IN {IMAGE_JOB_TYPES!r} AND status IN {statuses} "
        f"ORDER BY id LIMIT 1",
        (project_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    job_id, job_type, prompt, output_path = row
    return {"id": job_id, "job_type": job_type, "prompt": prompt, "output_path": output_path}


def mark_job(cursor, job_id: int, status: str, model_used: str = None, error: str = None):
    cursor.execute(
        "UPDATE jobs SET status = %s, model_used = %s, error = %s WHERE id = %s",
        (status, model_used, error, job_id),
    )


def generate_one(client: OpenAI, model_key: str, prompt: str, output_path: str):
    result = client.images.generate(model=model_key, prompt=prompt, size="1024x1024", n=1)
    image_b64 = result.data[0].b64_json
    image_bytes = base64.b64decode(image_b64)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(image_bytes)


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_image_jobs.py PROJECT_ID [--retry-failed] [--limit N]")
        sys.exit(1)

    project_id = sys.argv[1]
    include_failed = "--retry-failed" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set in .env — required for this worker.")

    client = OpenAI()

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        model_key = fetch_image_backend(cursor, project_id)
        print(f"Using model '{model_key}' (backend: openai_api) for '{project_id}'.")

        processed = 0
        while True:
            if limit is not None and processed >= limit:
                print(f"Reached --limit {limit}, stopping.")
                break

            job = fetch_next_job(cursor, project_id, include_failed)
            if not job:
                print("No more queued image jobs.")
                break

            print(f"[job {job['id']}] {job['job_type']} -> {job['output_path']}")
            try:
                generate_one(client, model_key, job["prompt"], job["output_path"])
                mark_job(cursor, job["id"], "done", model_used=model_key)
                conn.commit()
                print(f"  done.")
            except Exception as e:
                mark_job(cursor, job["id"], "failed", model_used=model_key, error=str(e))
                conn.commit()
                print(f"  FAILED: {e}")

            processed += 1

        print(f"Processed {processed} job(s) this run.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
