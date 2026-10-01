r"""Generate canonical assets and reference-guided shot images via OpenAI.

Run reference jobs first, review them with asset_review.py, then run shot
jobs. Shot jobs use images.edit with every approved character and location
reference; if a reference is unapproved, the job remains queued.

Usage: python run_openai_reference_jobs.py PROJECT_ID [--limit N]
"""
import base64
import json
import os
import sys
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")
DB = {"host": os.getenv("MYSQL_HOST", "mysql"), "port": int(os.getenv("MYSQL_PORT", "3306")),
      "user": os.getenv("MYSQL_USER", "glm_user"), "password": os.getenv("MYSQL_PASSWORD", "changeme"),
      "database": os.getenv("MYSQL_DATABASE", "glm_pipeline")}
REFERENCE_TYPES = ("character_reference", "location_reference", "prop_reference")


def next_job(cur, project_id):
    cur.execute("SELECT id,job_type,prompt,output_path,shot_id FROM jobs WHERE project_id=%s "
                "AND job_type IN ('character_reference','location_reference','prop_reference','shot_image') "
                "AND status='queued' ORDER BY FIELD(job_type,'character_reference','location_reference','prop_reference','shot_image'),id LIMIT 1", (project_id,))
    row = cur.fetchone()
    return dict(zip(("id", "job_type", "prompt", "output_path", "shot_id"), row)) if row else None


def approved(cur, project_id, asset_type, entity_id):
    cur.execute("SELECT id,output_path FROM asset_records WHERE project_id=%s AND asset_type=%s AND entity_id=%s "
                "AND status='approved' ORDER BY reviewed_at DESC,id DESC LIMIT 1", (project_id, asset_type, entity_id))
    return cur.fetchone()


def references_for_shot(cur, project_id, shot_id):
    cur.execute("SELECT scene_id,character_ids FROM shots WHERE shot_id=%s", (shot_id,))
    scene_id, characters = cur.fetchone()
    references = []
    for character_id in json.loads(characters) if characters else []:
        asset = approved(cur, project_id, "character_reference", character_id)
        if not asset:
            return [], f"character reference {character_id} is not approved"
        references.append((asset[0], asset[1], "character_identity"))
    cur.execute("SELECT location_id FROM scene_plan WHERE scene_id=%s", (scene_id,))
    location = cur.fetchone()
    if location and location[0]:
        asset = approved(cur, project_id, "location_reference", location[0])
        if not asset:
            return [], f"location reference {location[0]} is not approved"
        references.append((asset[0], asset[1], "location"))
    return (references, None) if references else ([], "no approved references")


def entity_id(cur, job):
    if job["job_type"] == "shot_image":
        return job["shot_id"]
    column = {"character_reference": "character_id", "location_reference": "location_id", "prop_reference": "prop_id"}[job["job_type"]]
    cur.execute(f"SELECT {column} FROM jobs WHERE id=%s", (job["id"],))
    return cur.fetchone()[0]


def record_candidate(cur, project_id, job, model, references):
    cur.execute("INSERT INTO asset_records (project_id,job_id,asset_type,entity_id,output_path,status,generation_backend,generation_model) "
                "VALUES (%s,%s,%s,%s,%s,'candidate','openai_api',%s)",
                (project_id, job["id"], job["job_type"], entity_id(cur, job), job["output_path"], model))
    asset_id = cur.lastrowid
    for order, (ref_id, _, role) in enumerate(references, 1):
        cur.execute("INSERT INTO job_asset_references (job_id,asset_id,reference_role,reference_order) VALUES (%s,%s,%s,%s)", (job["id"], ref_id, role, order))
    return asset_id


def render(client, model, job, references):
    output = Path(job["output_path"]); output.parent.mkdir(parents=True, exist_ok=True)
    if job["job_type"] in REFERENCE_TYPES:
        response = client.images.generate(model=model, prompt=job["prompt"], size="1024x1536", quality="high", output_format="png", n=1)
    else:
        files = []
        try:
            for _, reference_path, _ in references:
                path = Path(reference_path)
                if not path.exists():
                    raise RuntimeError(f"Approved reference file missing: {path}")
                files.append(open(path, "rb"))
            roles = "; ".join(f"Image {i + 1}: {role.replace('_', ' ')}" for i, (_, _, role) in enumerate(references))
            prompt = f"Create a new cinematic shot from these approved references. {roles}. Preserve identity, wardrobe and location continuity.\n\nSHOT:\n{job['prompt']}"
            response = client.images.edit(model=model, image=files, prompt=prompt, size="1024x1536", quality="high", output_format="png")
        finally:
            for file in files: file.close()
    output.write_bytes(base64.b64decode(response.data[0].b64_json))


def main():
    if len(sys.argv) < 2: raise SystemExit(__doc__)
    project_id = sys.argv[1]
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    if not os.getenv("OPENAI_API_KEY"): raise SystemExit("OPENAI_API_KEY is required.")
    conn = mysql.connector.connect(**DB); cur = conn.cursor()
    try:
        cur.execute("SELECT pmc.image_model_key FROM project_model_config pmc JOIN model_registry mr ON mr.model_key=pmc.image_model_key WHERE pmc.project_id=%s AND mr.backend='openai_api'", (project_id,))
        row = cur.fetchone()
        if not row: raise SystemExit("Lock an OpenAI image model for this project first.")
        model = row[0]; client = OpenAI(); processed = 0; failed = False
        while limit is None or processed < limit:
            job = next_job(cur, project_id)
            if not job: break
            references, blocker = references_for_shot(cur, project_id, job["shot_id"]) if job["job_type"] == "shot_image" else ([], None)
            if blocker:
                print(f"BLOCKED job {job['id']}: {blocker}"); break
            cur.execute("UPDATE jobs SET status='running' WHERE id=%s AND status='queued'", (job['id'],))
            claimed = cur.rowcount
            conn.commit()
            if claimed != 1: continue
            try:
                render(client, model, job, references)
                cur.execute("UPDATE jobs SET status='done',model_used=%s,error=NULL WHERE id=%s", (model, job["id"]))
                print(f"Created candidate asset {record_candidate(cur, project_id, job, model, references)} for job {job['id']}.")
                conn.commit(); processed += 1
            except Exception as exc:
                conn.rollback()
                cur.execute("UPDATE jobs SET status='failed',model_used=%s,error=%s WHERE id=%s", (model, str(exc), job["id"]))
                conn.commit(); print(f"FAILED job {job['id']}: {exc}"); processed += 1; failed = True
                break
        print(f"Processed {processed} job(s).")
    finally:
        cur.close(); conn.close()
    if failed: raise SystemExit(1)


if __name__ == "__main__": main()
