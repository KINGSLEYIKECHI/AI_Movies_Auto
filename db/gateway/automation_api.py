"""Small local API consumed by n8n; it never exposes the database publicly."""
import os
import subprocess
import sys
from pathlib import Path

import mysql.connector
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB = {"host": os.getenv("MYSQL_HOST", "mysql"), "port": int(os.getenv("MYSQL_PORT", "3306")),
      "user": os.getenv("MYSQL_USER", "glm_user"), "password": os.getenv("MYSQL_PASSWORD", "changeme"),
      "database": os.getenv("MYSQL_DATABASE", "glm_pipeline")}
app = FastAPI(title="GLM Film Automation", docs_url=None, redoc_url=None)


class Review(BaseModel):
    status: str


def query(sql, params=()):
    conn = mysql.connector.connect(**DB); cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params); rows = cur.fetchall(); conn.commit(); return rows, cur.rowcount
    finally:
        cur.close(); conn.close()


@app.get("/health")
def health():
    query("SELECT 1")
    return {"ok": True}


@app.get("/projects/{project_id}/review-assets")
def review_assets(project_id: str, status: str = "candidate"):
    rows, _ = query("SELECT id,asset_type,entity_id,output_path,generation_model,created_at FROM asset_records "
                    "WHERE project_id=%s AND status=%s ORDER BY id", (project_id, status))
    return {"assets": rows}


@app.post("/projects/{project_id}/assets/{asset_id}/review")
def review_asset(project_id: str, asset_id: int, body: Review):
    if body.status not in {"approved", "rejected"}:
        raise HTTPException(400, "status must be approved or rejected")
    _, changed = query("UPDATE asset_records SET status=%s,reviewed_at=NOW() WHERE id=%s AND project_id=%s",
                       (body.status, asset_id, project_id))
    if changed != 1: raise HTTPException(404, "asset not found")
    return {"id": asset_id, "status": body.status}


@app.post("/projects/{project_id}/workers/{worker}")
def run_worker(project_id: str, worker: str, limit: int | None = None):
    scripts = {"openai-references": "run_openai_reference_jobs.py", "comfy-references": "run_comfyui_reference_jobs.py"}
    if worker not in scripts: raise HTTPException(404, "unknown worker")
    command = [sys.executable, scripts[worker], project_id]
    if limit is not None: command += ["--limit", str(limit)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    if result.returncode: raise HTTPException(500, result.stderr[-2000:] or result.stdout[-2000:])
    return {"worker": worker, "output": result.stdout[-4000:]}
