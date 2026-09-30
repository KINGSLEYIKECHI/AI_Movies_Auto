r"""Render local ComfyUI text-to-image reference jobs.

This is the local fallback when OpenAI image credits are unavailable. It
supports only canonical reference jobs on purpose: no local image-to-image
workflow has been approved yet, so it will never create inconsistent
text-only shot frames as a hidden fallback.

Usage: python run_comfyui_reference_jobs.py PROJECT_ID [--retry-failed] [--limit N]
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import mysql.connector
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
DB = {"host": os.getenv("MYSQL_HOST", "mysql"), "port": int(os.getenv("MYSQL_PORT", "3306")),
      "user": os.getenv("MYSQL_USER", "glm_user"), "password": os.getenv("MYSQL_PASSWORD", "changeme"),
      "database": os.getenv("MYSQL_DATABASE", "glm_pipeline")}
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://host.docker.internal:8188").rstrip("/")
TYPES = ("character_reference", "location_reference", "prop_reference")


def next_job(cur, project_id, retry):
    states = "('queued','failed')" if retry else "('queued')"
    cur.execute("SELECT id,job_type,prompt,output_path FROM jobs WHERE project_id=%s AND job_type IN "
                "('character_reference','location_reference','prop_reference') AND status IN " + states + " ORDER BY id LIMIT 1", (project_id,))
    row = cur.fetchone()
    return dict(zip(("id", "job_type", "prompt", "output_path"), row)) if row else None


def model_config(cur, project_id):
    cur.execute("SELECT pmf.model_key,mr.workflow_template_path,mr.backend FROM project_model_fallbacks pmf "
                "JOIN model_registry mr ON mr.model_key=pmf.model_key WHERE pmf.project_id=%s AND pmf.model_type='image' "
                "ORDER BY pmf.priority LIMIT 1", (project_id,))
    row = cur.fetchone()
    if not row: raise SystemExit("Project has no configured local image fallback. Add one to project_model_fallbacks first.")
    if row[2] != "comfyui": raise SystemExit(f"Project is locked to {row[0]} ({row[2]}), not a local ComfyUI model.")
    if not row[1]: raise SystemExit(f"No ComfyUI workflow_template_path is registered for {row[0]}.")
    return row[0], Path(row[1])


def patch_flux2_workflow(template, prompt):
    """Patch the supplied Flux 2 Klein API exports without changing them."""
    workflow = json.loads(template.read_text(encoding="utf-8"))
    if "76" in workflow: workflow["76"]["inputs"]["value"] = prompt
    elif "75:74" in workflow: workflow["75:74"]["inputs"]["text"] = prompt
    else: raise RuntimeError("Workflow has no known Flux prompt node (76 or 75:74).")
    if "75:73" in workflow: workflow["75:73"]["inputs"]["noise_seed"] = int.from_bytes(os.urandom(8), "big") >> 1
    return workflow


def wait_for_image(prompt_id):
    deadline = time.monotonic() + int(os.getenv("COMFYUI_TIMEOUT_SECONDS", "1800"))
    while time.monotonic() < deadline:
        history = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=30).json()
        record = history.get(prompt_id)
        if record:
            for output in record.get("outputs", {}).values():
                images = output.get("images", [])
                if images: return images[0]
            if record.get("status", {}).get("status_str") == "error":
                raise RuntimeError(str(record.get("status")))
        time.sleep(2)
    raise TimeoutError(f"ComfyUI job {prompt_id} exceeded timeout.")


def candidate(cur, project_id, job, model):
    column = {"character_reference":"character_id", "location_reference":"location_id", "prop_reference":"prop_id"}[job["job_type"]]
    cur.execute(f"SELECT {column} FROM jobs WHERE id=%s", (job["id"],)); entity = cur.fetchone()[0]
    cur.execute("INSERT INTO asset_records (project_id,job_id,asset_type,entity_id,output_path,status,generation_backend,generation_model) "
                "VALUES (%s,%s,%s,%s,%s,'candidate','comfyui',%s)", (project_id,job["id"],job["job_type"],entity,job["output_path"],model))
    return cur.lastrowid


def main():
    if len(sys.argv) < 2: raise SystemExit(__doc__)
    project_id = sys.argv[1]; retry = "--retry-failed" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    conn = mysql.connector.connect(**DB); cur = conn.cursor()
    try:
        model, template = model_config(cur, project_id); processed = 0
        while limit is None or processed < limit:
            job = next_job(cur, project_id, retry)
            if not job: break
            try:
                workflow = patch_flux2_workflow(template, job["prompt"])
                response = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow, "client_id": str(uuid.uuid4())}, timeout=30)
                response.raise_for_status(); image = wait_for_image(response.json()["prompt_id"])
                image_data = requests.get(f"{COMFYUI_URL}/view?" + urlencode(image), timeout=120); image_data.raise_for_status()
                output = Path(job["output_path"]); output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(image_data.content)
                cur.execute("UPDATE jobs SET status='done',model_used=%s,error=NULL WHERE id=%s", (model,job["id"]))
                print(f"Created candidate asset {candidate(cur, project_id, job, model)} for job {job['id']}.")
                conn.commit()
            except Exception as exc:
                cur.execute("UPDATE jobs SET status='failed',model_used=%s,error=%s WHERE id=%s", (model,str(exc),job["id"])); conn.commit(); print(f"FAILED job {job['id']}: {exc}")
            processed += 1
    finally:
        cur.close(); conn.close()


if __name__ == "__main__": main()
