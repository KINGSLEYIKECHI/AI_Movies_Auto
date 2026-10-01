"""Local production control API. Run with one uvicorn process."""
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import mysql.connector

DB = {"host": os.getenv("MYSQL_HOST", "mysql"), "port": int(os.getenv("MYSQL_PORT", "3306")),
      "user": os.getenv("MYSQL_USER", "glm_user"), "password": os.getenv("MYSQL_PASSWORD", "changeme"),
      "database": os.getenv("MYSQL_DATABASE", "glm_pipeline")}
ROOT = Path(os.getenv("PROJECTS_BASE_PATH", "/ai_movies")).resolve()
RUNS = Path(os.getenv("AUTOMATION_RUN_PATH", str(Path(__file__).parent / "outputs" / "runs")))
app = FastAPI(title="GLM Film Automation")
lock = threading.Lock()
active = False

class Review(BaseModel):
    status: str

class RunOptions(BaseModel):
    limit: int = Field(default=1, ge=1, le=100)
    episode_id: str | None = None

def query(sql, params=()):
    conn = mysql.connector.connect(**DB)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.with_rows else []
        conn.commit()
        return rows, cur.rowcount
    finally:
        cur.close(); conn.close()

def project(project_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id):
        raise HTTPException(400, "Invalid project ID")
    rows, _ = query("SELECT project_id FROM projects WHERE project_id=%s", (project_id,))
    if not rows: raise HTTPException(404, "Project not found")

def save_run(record):
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / (record["id"] + ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(path)

@app.get("/health")
def health():
    try: query("SELECT 1")
    except mysql.connector.Error:
        raise HTTPException(503, "Database unavailable")
    return {"ok": True}

@app.get("/review")
def review_page():
    return FileResponse(Path(__file__).parent / "review.html")

@app.get("/projects")
def projects():
    rows, _ = query("SELECT project_id FROM projects ORDER BY project_id")
    return {"projects": rows}

@app.get("/projects/{project_id}/status")
def status(project_id: str):
    project(project_id)
    rows, _ = query("SELECT job_type,status,COUNT(*) AS count FROM jobs WHERE project_id=%s GROUP BY job_type,status", (project_id,))
    return {"jobs": rows}

@app.get("/projects/{project_id}/review-assets")
def review_assets(project_id: str, status: str = "candidate"):
    project(project_id)
    if status not in {"candidate", "approved", "rejected", "superseded"}:
        raise HTTPException(400, "Invalid asset status")
    rows, _ = query("SELECT id,status,asset_type,entity_id,output_path,generation_model,created_at FROM asset_records WHERE project_id=%s AND status=%s ORDER BY id", (project_id, status))
    for row in rows: row["preview_url"] = f"/projects/{project_id}/assets/{row['id']}/file"
    return {"assets": rows}

@app.get("/projects/{project_id}/assets/{asset_id}/file")
def asset_file(project_id: str, asset_id: int):
    rows, _ = query("SELECT output_path FROM asset_records WHERE project_id=%s AND id=%s", (project_id, asset_id))
    if not rows: raise HTTPException(404, "Asset not found")
    path = Path(rows[0]["output_path"]).resolve()
    if not path.is_relative_to(ROOT / project_id) or not path.is_file():
        raise HTTPException(404, "Asset file unavailable in project folder")
    return FileResponse(path)

@app.post("/projects/{project_id}/assets/{asset_id}/review")
def review_asset(project_id: str, asset_id: int, body: Review):
    if body.status not in {"approved", "rejected"}: raise HTTPException(400, "Invalid review status")
    _, changed = query("UPDATE asset_records SET status=%s,reviewed_at=NOW() WHERE id=%s AND project_id=%s AND status='candidate'", (body.status, asset_id, project_id))
    if changed != 1: raise HTTPException(409, "Asset is missing or already reviewed")
    return {"id": asset_id, "status": body.status}

@app.post("/projects/{project_id}/jobs/{job_id}/retry")
def retry(project_id: str, job_id: int):
    _, changed = query("UPDATE jobs SET status='queued',error=NULL WHERE id=%s AND project_id=%s AND status='failed'", (job_id, project_id))
    if changed != 1: raise HTTPException(409, "Only failed jobs can be retried")
    return {"id": job_id, "status": "queued"}

@app.post("/projects/{project_id}/assets/{asset_id}/regenerate", status_code=202)
def regenerate(project_id: str, asset_id: int):
    project(project_id)
    rows, _ = query("SELECT a.status,j.* FROM asset_records a JOIN jobs j ON j.id=a.job_id WHERE a.project_id=%s AND a.id=%s AND a.status='rejected'", (project_id, asset_id))
    if not rows: raise HTTPException(409, "Reject a generated asset before requesting a new version")
    job = rows[0]
    original = Path(job['output_path']).resolve()
    if not original.is_relative_to(ROOT / project_id): raise HTTPException(400, "Asset path outside project")
    output = original.with_name(original.stem + '_' + uuid.uuid4().hex[:12] + original.suffix)
    query("INSERT INTO jobs(project_id,episode_id,scene_id,shot_id,character_id,location_id,prop_id,job_type,prompt,output_path) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", tuple(job.get(key) for key in ('project_id','episode_id','scene_id','shot_id','character_id','location_id','prop_id','job_type','prompt')) + (str(output),))
    return {"status": "queued", "output_path": str(output)}

@app.post("/projects/{project_id}/queue/{stage}")
def queue(project_id: str, stage: str):
    project(project_id)
    if stage not in {"references", "shot-images", "shot-videos"}: raise HTTPException(400, "Unknown stage")
    result = subprocess.run([sys.executable, str(Path(__file__).parent / "enqueue_asset_jobs.py"), project_id, stage], capture_output=True, text=True, timeout=60)
    if result.returncode: raise HTTPException(500, "Queue failed; check gateway logs and schema migration")
    return {"output": result.stdout}

@app.post("/projects/{project_id}/workers/{worker}", status_code=202)
def run_worker(project_id: str, worker: str, body: RunOptions = RunOptions()):
    global active
    project(project_id)
    scripts = {"openai-references": "run_openai_reference_jobs.py", "comfy-references": "run_comfyui_reference_jobs.py", "comfy-videos": "run_comfyui_video_jobs.py", "assembly": "assemble_episode.py", "pipeline": "advance_pipeline.py"}
    if worker not in scripts: raise HTTPException(404, "Unknown worker")
    command = [sys.executable, "-u", str(Path(__file__).parent / scripts[worker]), project_id]
    if worker == "assembly":
        if not body.episode_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body.episode_id):
            raise HTTPException(400, "Assembly requires episode_id")
        command.append(body.episode_id)
    else: command += ["--limit", str(body.limit)]
    with lock:
        if active: raise HTTPException(409, "A worker is running; wait for its run to finish")
        active = True
    record = {"id": str(uuid.uuid4()), "project_id": project_id, "worker": worker, "status": "running"}
    try: save_run(record)
    except Exception:
        with lock: active = False
        raise
    def execute():
        global active
        try:
            with (RUNS / (record["id"] + ".log")).open("w", encoding="utf-8") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=int(os.getenv("WORKER_TIMEOUT_SECONDS", "14400")))
            record.update(status="done" if result.returncode == 0 else "failed", returncode=result.returncode)
        except Exception as exc: record.update(status="failed", error=type(exc).__name__)
        finally:
            try: save_run(record)
            finally:
                with lock: active = False
    threading.Thread(target=execute, daemon=True).start()
    return {**record, "status_url": f"/runs/{record['id']}"}

@app.get("/runs/{run_id}")
def run_status(run_id: str):
    try: uuid.UUID(run_id)
    except ValueError: raise HTTPException(400, "Invalid run ID")
    path = RUNS / (run_id + ".json")
    if not path.is_file(): raise HTTPException(404, "Run not found")
    record = json.loads(path.read_text(encoding="utf-8"))
    log = path.with_suffix(".log")
    record["output"] = log.read_text(encoding="utf-8", errors="replace")[-8000:] if log.exists() else ""
    return record

@app.on_event("startup")
def recover_runs():
    # A process restart interrupts its workers; never silently retry paid work.
    for path in RUNS.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status") == "running":
            record.update(status="interrupted")
            save_run(record)
