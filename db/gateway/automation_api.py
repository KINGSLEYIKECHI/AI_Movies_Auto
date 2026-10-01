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
from datetime import datetime, timezone
import time
import requests
from production_control import ProductionSpec, preview, read_settings, write_settings, root, can_finish_stage_before_review
from run_comfyui_reference_jobs import COMFYUI_URL
from pydantic import BaseModel, Field
import mysql.connector
from asset_control import IMAGE_TYPES, MAX_UPLOAD_BYTES, UNRESOLVED_REJECTIONS, project_folder, scoped_file, save_upload, upload_reference, write_options, read_options
from project_cleanup import delete_project, recover_cleanup

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

class AssetRevision(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=24000)
    use_source_image: bool = False
    reference_upload_ids: list[str] = Field(default_factory=list, max_length=3)
    image_model_key: str | None = None

class ReferenceUpload(BaseModel):
    name: str = Field(max_length=200)
    data: str = Field(max_length=MAX_UPLOAD_BYTES * 4 // 3 + 8)
    role: str = 'style'

class DeleteConfirmation(BaseModel):
    confirm_project_id: str

def pause_for_change(project_id):
    settings = read_settings(project_id)
    if settings:
        settings.update(automation_enabled=False, prompts_approved=False)
        write_settings(project_id, settings)

def require_idle(project_id):
    if active: raise HTTPException(409, 'Wait for the current worker to finish')
    rows, _ = query("SELECT id FROM jobs WHERE project_id=%s AND status='running' LIMIT 1", (project_id,))
    if rows: raise HTTPException(409, 'This project has running jobs; resolve the external submission first')

@app.get('/projects/{project_id}/delete-preview')
def deletion_preview(project_id: str):
    project(project_id)
    try:
        folder = project_folder(project_id, ROOT)
    except ValueError as exc: raise HTTPException(409, str(exc))
    counts, _ = query('SELECT (SELECT COUNT(*) FROM jobs WHERE project_id=%s) AS jobs,(SELECT COUNT(*) FROM asset_records WHERE project_id=%s) AS assets', (project_id, project_id))
    return {'project_id': project_id, 'folder': str(folder), **(counts[0] if counts else {})}

@app.delete('/projects/{project_id}')
def remove_project(project_id: str, body: DeleteConfirmation):
    if body.confirm_project_id != project_id: raise HTTPException(400, 'Confirm the exact project ID')
    try: project_folder(project_id, ROOT)
    except ValueError as exc: raise HTTPException(400, str(exc))
    with lock:
        require_idle(project_id)
        conn = mysql.connector.connect(**DB)
        try:
            pending = recover_cleanup(conn, ROOT, RUNS)
            rows, _ = query('SELECT project_id FROM projects WHERE project_id=%s', (project_id,))
            if not rows:
                return {'deleted': True, 'cleanup_complete': project_id not in pending}
            return delete_project(conn, project_id, ROOT, RUNS)
        except ValueError as exc: raise HTTPException(409, str(exc))
        except (OSError, mysql.connector.Error): raise HTTPException(503, 'Cleanup failed. Inspect gateway logs and retry; deletion was not confirmed complete')
        finally: conn.close()

@app.get('/projects/{project_id}/references')
def uploaded_references(project_id: str):
    project(project_id)
    records = []
    for path in (project_folder(project_id, ROOT) / 'uploads').glob('*.json'):
        try:
            record, _ = upload_reference(project_id, path.stem, ROOT)
            record['preview_url'] = f'/projects/{project_id}/references/{path.stem}/file'
            records.append(record)
        except (ValueError, OSError): continue
    return {'references': records}

@app.post('/projects/{project_id}/references', status_code=201)
def upload_reference_image(project_id: str, body: ReferenceUpload):
    if body.role not in {'identity', 'style', 'composition'}: raise HTTPException(400, 'Unknown reference role')
    with lock:
        project(project_id)
        try: return save_upload(project_id, body.data, body.name, body.role, ROOT)
        except ValueError as exc: raise HTTPException(400, str(exc))

@app.get('/projects/{project_id}/references/{reference_id}/file')
def reference_file(project_id: str, reference_id: str):
    project(project_id)
    try: _, path = upload_reference(project_id, reference_id, ROOT)
    except ValueError as exc: raise HTTPException(404, str(exc))
    return FileResponse(path)

class RunOptions(BaseModel):
    limit: int = Field(default=1, ge=1, le=100)
    episode_id: str | None = None
    job_id: int | None = Field(default=None, ge=1)
    stage: str | None = None

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
    return FileResponse(Path(__file__).parent / "review.html",headers={'Cache-Control':'no-store'})

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
    with lock:
        project(project_id)
        require_idle(project_id)
        conn = mysql.connector.connect(**DB); cur = conn.cursor(dictionary=True)
        try:
            cur.execute('SELECT * FROM asset_records WHERE id=%s AND project_id=%s FOR UPDATE', (asset_id, project_id))
            asset = cur.fetchone()
            if not asset: raise HTTPException(404, 'Asset not found')
            if asset['status'] == body.status: return {'id': asset_id, 'status': body.status, 'affected_assets': 0}
            # Changing an approved choice or rejecting a candidate pauses automatic work.
            if body.status == 'rejected' or asset['status'] in {'approved', 'superseded'}: pause_for_change(project_id)
            if body.status == 'approved':
                cur.execute("UPDATE asset_records SET status='superseded' WHERE project_id=%s AND asset_type=%s AND entity_id <=> %s AND status='approved' AND id<>%s", (project_id, asset['asset_type'], asset['entity_id'], asset_id))
            cur.execute('UPDATE asset_records SET status=%s,reviewed_at=NOW() WHERE id=%s AND project_id=%s', (body.status, asset_id, project_id))
            # A reference change requires checking already-produced descendants again.
            affected = 0
            if asset['asset_type'] in IMAGE_TYPES | {'shot_video'}:
                if asset['asset_type'].endswith('_reference'):
                    cur.execute("UPDATE asset_records SET status='candidate' WHERE project_id=%s AND status='approved' AND asset_type IN ('shot_image','shot_video','final_render')", (project_id,))
                elif asset['asset_type'] == 'shot_image':
                    cur.execute("UPDATE asset_records SET status='candidate' WHERE project_id=%s AND status='approved' AND ((asset_type='shot_video' AND entity_id=%s) OR asset_type='final_render')", (project_id, asset['entity_id']))
                else:
                    cur.execute("UPDATE asset_records SET status='candidate' WHERE project_id=%s AND status='approved' AND asset_type='final_render'", (project_id,))
                affected = cur.rowcount
                if affected: pause_for_change(project_id)
            conn.commit()
            return {'id': asset_id, 'status': body.status, 'affected_assets': affected}
        except Exception: conn.rollback(); raise
        finally: cur.close(); conn.close()

@app.post("/projects/{project_id}/jobs/{job_id}/retry")
def retry(project_id: str, job_id: int):
    _, changed = query("UPDATE jobs SET status='queued',error=NULL WHERE id=%s AND project_id=%s AND status='failed'", (job_id, project_id))
    if changed != 1: raise HTTPException(409, "Only failed jobs can be retried")
    return {"id": job_id, "status": "queued"}

@app.get('/projects/{project_id}/assets/{asset_id}/edit')
def asset_edit_details(project_id: str, asset_id: int):
    project(project_id)
    rows, _ = query('SELECT a.*,j.prompt AS original_prompt FROM asset_records a LEFT JOIN jobs j ON j.id=a.job_id AND j.project_id=a.project_id WHERE a.project_id=%s AND a.id=%s', (project_id, asset_id))
    if not rows: raise HTTPException(404, 'Asset not found')
    asset = rows[0]
    if not asset.get('original_prompt'):
        source, _ = query('SELECT prompt FROM jobs WHERE project_id=%s AND job_type=%s AND COALESCE(shot_id,character_id,location_id,prop_id)=%s ORDER BY id DESC LIMIT 1', (project_id, asset['asset_type'], asset['entity_id']))
        asset['original_prompt'] = source[0]['prompt'] if source else 'Create a cinematic ' + asset['asset_type'].replace('_', ' ') + ' for ' + str(asset['entity_id'] or '') + '.'
    asset['can_edit_image'] = asset['asset_type'] in IMAGE_TYPES
    return asset

@app.post("/projects/{project_id}/assets/{asset_id}/regenerate", status_code=202)
def regenerate(project_id: str, asset_id: int, body: AssetRevision = AssetRevision()):
    with lock:
        project(project_id)
        require_idle(project_id)
        asset = asset_edit_details(project_id, asset_id)
        kind = asset['asset_type']
        if kind not in IMAGE_TYPES | {'shot_video'}: raise HTTPException(400, 'Use Assemble another cut for final exports; this asset type has no render worker')
        if kind == 'shot_video' and (body.use_source_image or body.image_model_key): raise HTTPException(400, 'Videos are regenerated from a starting image and video prompt; GPT image editing applies to images')
        if kind == 'shot_video' and len(body.reference_upload_ids) > 1: raise HTTPException(400, 'Choose one optional starting frame for video')
        options = {'source_asset_id': asset_id, 'use_source_image': body.use_source_image, 'reference_upload_ids': body.reference_upload_ids, 'image_model_key': body.image_model_key}
        for identifier in body.reference_upload_ids:
            try: upload_reference(project_id, identifier, ROOT)
            except ValueError as exc: raise HTTPException(400, str(exc))
        if body.image_model_key:
            models, _ = query("SELECT model_key FROM model_registry WHERE model_key=%s AND model_type='image' AND backend='openai_api'", (body.image_model_key,))
            if not models: raise HTTPException(400, 'Choose a registered OpenAI image model')
        try:
            original = scoped_file(project_id, asset['output_path'], ROOT)
            if body.use_source_image and not original.is_file(): raise ValueError('Original image file is missing. Regenerate without the original image instead')
        except ValueError as exc: raise HTTPException(400, str(exc))
        conn = mysql.connector.connect(**DB); cur = conn.cursor(dictionary=True)
        output = original.with_name(kind + '_' + str(asset_id) + '_' + uuid.uuid4().hex[:12] + ('.mp4' if kind == 'shot_video' else '.png'))
        try:
            cur.execute('SELECT * FROM jobs WHERE id=%s AND project_id=%s', (asset.get('job_id'), project_id))
            job = cur.fetchone()
            if not job:
                cur.execute('SELECT * FROM jobs WHERE project_id=%s AND job_type=%s AND COALESCE(shot_id,character_id,location_id,prop_id)=%s ORDER BY id DESC LIMIT 1', (project_id, kind, asset['entity_id']))
                job = cur.fetchone()
            # Legacy imported assets can have no job_id; rebuild the entity link.
            if not job:
                job = {'project_id': project_id, 'job_type': kind}
                column = {'character_reference': 'character_id', 'location_reference': 'location_id', 'prop_reference': 'prop_id', 'shot_image': 'shot_id', 'shot_video': 'shot_id'}[kind]
                job[column] = asset['entity_id']
                if column == 'shot_id':
                    cur.execute('SELECT s.scene_id,sp.episode_id FROM shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id WHERE s.shot_id=%s AND sp.project_id=%s', (asset['entity_id'], project_id))
                    shot = cur.fetchone()
                    if not shot: raise HTTPException(409, 'The legacy asset has no matching planned shot')
                    job.update(shot)
            pause_for_change(project_id)
            job['prompt'] = body.prompt or asset['original_prompt']
            columns = ('project_id','episode_id','scene_id','shot_id','character_id','location_id','prop_id','job_type','prompt')
            cur.execute('INSERT INTO jobs(project_id,episode_id,scene_id,shot_id,character_id,location_id,prop_id,job_type,prompt,output_path) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', tuple(job.get(key) for key in columns) + (str(output),))
            job_id = cur.lastrowid
            write_options(project_id, output, options, ROOT)
            cur.execute("UPDATE asset_records SET metadata=JSON_SET(COALESCE(metadata,JSON_OBJECT()),'$.replacement_job_id',%s) WHERE id=%s AND project_id=%s", (job_id, asset_id, project_id))
            conn.commit()
            return {'id': job_id, 'status': 'queued', 'output_path': str(output), 'message': 'New version queued. The original is preserved. Review the prompt, then render the replacement.'}
        except Exception:
            conn.rollback()
            Path(str(output) + '.options.json').unlink(missing_ok=True)
            raise
        finally: cur.close(); conn.close()

@app.post("/projects/{project_id}/queue/{stage}")
def queue(project_id: str, stage: str):
    with lock:
        project(project_id)
        if active or (read_settings(project_id) or {}).get('automation_enabled'):raise HTTPException(409,'Pause automatic production and wait for the active worker before queuing manual jobs')
        if stage not in {"references", "shot-images", "shot-videos"}: raise HTTPException(400, "Unknown stage")
        result = subprocess.run([sys.executable, str(Path(__file__).parent / "enqueue_asset_jobs.py"), project_id, stage], capture_output=True, text=True, timeout=60)
        if result.returncode: raise HTTPException(500, "Queue failed; check gateway logs and schema migration")
        return {"output": result.stdout}

@app.post("/projects/{project_id}/workers/{worker}", status_code=202)
def run_worker(project_id: str, worker: str, body: RunOptions = RunOptions()):
    global active
    project(project_id)
    scripts = {"openai-references": "run_openai_reference_jobs.py", "comfy-references": "run_comfyui_reference_jobs.py", "comfy-videos": "run_comfyui_video_jobs.py", "assembly": "assemble_episode.py", "pipeline": "advance_pipeline.py", "planning": "plan_production.py"}
    if worker == "planning":
        settings = read_settings(project_id)
        if not settings: raise HTTPException(400, "This project has no saved production setup")
        if settings.get("planning_status") == "ready": raise HTTPException(409, "The plan is complete. Create a new production to change its structure.")
    if worker not in scripts: raise HTTPException(404, "Unknown worker")
    command = [sys.executable, "-u", str(Path(__file__).parent / scripts[worker]), project_id]
    if worker == "assembly":
        if not body.episode_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body.episode_id):
            raise HTTPException(400, "Assembly requires episode_id")
        command.append(body.episode_id)
    elif worker != "planning": command += ["--limit", str(body.limit)]
    if body.stage:
        if worker!='openai-references' or body.stage not in {'references','shot-images'}:raise HTTPException(400,'Select references or shot-images for the OpenAI image worker')
        command += ['--stage',body.stage]
    if body.job_id:
        if worker not in {'openai-references', 'comfy-videos'}: raise HTTPException(400, 'Individual rendering is supported for OpenAI images and ComfyUI video')
        rows, _ = query("SELECT job_type FROM jobs WHERE id=%s AND project_id=%s AND status='queued'", (body.job_id, project_id))
        if not rows or (worker == 'comfy-videos') != (rows[0]['job_type'] == 'shot_video'): raise HTTPException(409, 'The selected queued job does not match this worker')
        command += ['--job-id', str(body.job_id)]
    return start_run(command, project_id, worker)


def start_run(command, project_id, worker, prepare=None):
    global active
    with lock:
        if active: raise HTTPException(409, "A worker is running; wait for its run to finish")
        active = True
    record = {"id": str(uuid.uuid4()), "project_id": project_id, "worker": worker, "status": "running", "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        if prepare: prepare()
        save_run(record)
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

class PromptEdit(BaseModel):
    prompt: str = Field(min_length=1, max_length=24000)

class ImprovePrompt(PromptEdit):
    instruction: str = Field(default='Make the prompt specific, cinematic and clear while preserving all facts and identity references.', min_length=1, max_length=2000)

class AutomationOptions(BaseModel):
    enabled: bool
    approve_prompts: bool = False

@app.get('/')
def home():
    return review_page()

@app.get('/interface/{filename}')
def interface_file(filename: str):
    if filename not in {'studio.js','studio.css'}:raise HTTPException(404,'Not found')
    return FileResponse(Path(__file__).parent/filename,headers={'Cache-Control':'no-store'})

@app.get('/projects/{project_id}/exports')
def production_exports(project_id: str):
    project(project_id)
    rows,_=query("SELECT id,status,asset_type,entity_id,output_path,generation_model,metadata FROM asset_records WHERE project_id=%s AND asset_type='final_render' ORDER BY id DESC",(project_id,))
    for row in rows:row['preview_url']=f"/projects/{project_id}/assets/{row['id']}/file"
    return {'assets':rows}

@app.get('/models')
def models():
    rows,_=query('SELECT model_key,model_type,backend,display_name,available FROM model_registry ORDER BY model_type,model_key')
    return {'models':rows,'default_image_model':os.getenv('OPENAI_IMAGE_MODEL','')}

@app.get('/video-options')
def video_options():
    try:
        response=requests.get(COMFYUI_URL+'/object_info/LoraLoaderModelOnly',timeout=5);response.raise_for_status()
        choices=response.json().get('LoraLoaderModelOnly',{}).get('input',{}).get('required',{}).get('lora_name',[[]])[0]
        return {'connected':True,'loras':choices,'note':'Choose a LoRA compatible with your selected video model.'}
    except requests.RequestException:
        return {'connected':False,'loras':[],'note':'Start ComfyUI to load the list of installed LoRAs.'}

@app.post('/productions/preview')
def production_preview(body: ProductionSpec):
    return preview(body)

@app.post('/productions',status_code=202)
def create_production(body: ProductionSpec):
    command=[sys.executable,'-u',str(Path(__file__).parent/'plan_production.py'),body.project_id]
    def prepare():
        existing,_=query('SELECT project_id FROM projects WHERE project_id=%s',(body.project_id,))
        if existing:raise HTTPException(409,'Project ID already exists. Select that project or use a new ID.')
        registered,_=query("SELECT model_key,model_type,backend FROM model_registry WHERE model_key IN (%s,%s)",(body.image_model_key,body.video_model_key))
        if not any(row['model_key']==body.image_model_key and row['model_type']=='image' and row['backend']=='openai_api' for row in registered):raise HTTPException(400,'Select a registered OpenAI image model')
        if not any(row['model_key']==body.video_model_key and row['model_type']=='video' and row['backend']=='comfyui' for row in registered):raise HTTPException(400,'Select a registered ComfyUI video model')
        if body.lora_mode=='custom':
            options=video_options()
            if body.lora_name not in options['loras']:raise HTTPException(400,'Selected LoRA is not available in ComfyUI')
        if root(body.project_id).exists(): raise HTTPException(409,'A folder already exists for this ID. Choose a new project ID to preserve its files.')
        query('INSERT INTO projects(project_id,title,premise,visual_style) VALUES(%s,%s,%s,%s)',(body.project_id,body.title,body.concept,body.visual_style))
        write_settings(body.project_id,{'spec':body.model_dump(),'planning_status':'pending','completed':[],'automation_enabled':False,'prompts_approved':False})
    return start_run(command,body.project_id,'planning',prepare)

@app.get('/projects/{project_id}/production')
def production_dashboard(project_id: str):
    project(project_id)
    settings=read_settings(project_id)
    counts,_=query('SELECT job_type,status,COUNT(*) AS count FROM jobs WHERE project_id=%s GROUP BY job_type,status',(project_id,))
    candidates,_=query("SELECT asset_type,COUNT(*) AS count FROM asset_records WHERE project_id=%s AND status='candidate' GROUP BY asset_type",(project_id,))
    failures,_=query("SELECT id,job_type,status,error FROM jobs WHERE project_id=%s AND status IN ('failed','running') ORDER BY id",(project_id,))
    episodes,_=query('SELECT DISTINCT episode_id FROM scene_plan WHERE project_id=%s ORDER BY episode_id',(project_id,))
    # Historical rejected versions stop automation only if no newer job exists.
    rejected,_=query(UNRESOLVED_REJECTIONS,(project_id,))
    next_action='continue';message='Ready to continue production.'
    planning=(settings or {}).get('planning_status')
    if planning in {'pending','running','failed'}:
        next_action='planning';message='Generating story, scenes and shot prompts.' if planning!='failed' else 'Planning stopped. Inspect the run, correct the issue and resume planning.'
    elif settings and not settings.get('prompts_approved'):
        next_action='prompts';message='Review the computed prompts, then start production.'
    elif failures:
        next_action='failure';message='A render is running or needs attention. Inspect its run before retrying.'
    elif candidates and not (settings and can_finish_stage_before_review([row['asset_type'] for row in candidates],[row['job_type'] for row in counts if row['status']=='queued'],settings['spec'].get('review_mode','every_stage'))):
        next_action='review';message='Review the new assets to continue.'
    elif rejected:
        next_action='rejected';message='Queue a replacement for each rejected asset to continue.'
    elif not episodes:
        next_action='no_plan';message='This project needs a scene and shot plan.'
    else:
        exports,_=query("SELECT DISTINCT entity_id FROM asset_records WHERE project_id=%s AND asset_type='final_render' AND status='approved'",(project_id,))
        if {row['episode_id'] for row in episodes}<={row['entity_id'] for row in exports}:next_action='complete';message='All planned episodes have approved final exports.'
    runs=[]
    for path in RUNS.glob('*.json'):
        try:
            run=json.loads(path.read_text(encoding='utf-8'))
            if run.get('project_id')==project_id:runs.append(run)
        except (OSError,ValueError):continue
    runs.sort(key=lambda r:r.get('created_at',''),reverse=True)
    return {'settings':settings,'jobs':counts,'reviews':candidates,'failures':failures,'episodes':episodes,'next_action':next_action,'message':message,'runs':runs[:5],'worker_busy':active}

@app.get('/projects/{project_id}/prompts')
def production_prompts(project_id: str):
    project(project_id)
    rows,_=query("SELECT id,job_type,scene_id,shot_id,prompt,status FROM jobs WHERE project_id=%s AND job_type IN ('character_reference','location_reference','prop_reference','shot_image','shot_video') ORDER BY id",(project_id,))
    assets,_=query('SELECT job_id,id AS asset_id FROM asset_records WHERE project_id=%s AND job_id IS NOT NULL ORDER BY id',(project_id,))
    linked={row['job_id']:row['asset_id'] for row in assets}
    for row in rows:row['asset_id']=linked.get(row['id'])
    planned,_=query("SELECT s.shot_id,s.scene_id,s.video_prompt AS prompt,s.duration_seconds FROM shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id WHERE sp.project_id=%s ORDER BY sp.scene_number,s.shot_id",(project_id,))
    return {'prompts':rows,'video_prompts':planned}

@app.get('/projects/{project_id}/jobs/{job_id}/options')
def job_options(project_id: str, job_id: int):
    project(project_id)
    rows, _ = query('SELECT output_path FROM jobs WHERE id=%s AND project_id=%s', (job_id, project_id))
    if not rows: raise HTTPException(404, 'Job not found')
    return read_options(project_id, rows[0]['output_path'], ROOT)

@app.put('/projects/{project_id}/jobs/{job_id}/options')
def edit_job_options(project_id: str, job_id: int, body: AssetRevision):
    with lock:
        project(project_id)
        require_idle(project_id)
        if (read_settings(project_id) or {}).get('automation_enabled'): raise HTTPException(409, 'Pause automatic production before editing render inputs')
        rows, _ = query("SELECT output_path,job_type FROM jobs WHERE id=%s AND project_id=%s AND status IN ('queued','failed')", (job_id, project_id))
        if not rows: raise HTTPException(409, 'Only queued or failed render inputs can be edited')
        job = rows[0]
        if job['job_type'] not in IMAGE_TYPES | {'shot_video'}: raise HTTPException(400, 'Unsupported render type')
        options = read_options(project_id, job['output_path'], ROOT)
        if body.use_source_image and not options.get('source_asset_id'): raise HTTPException(400, 'This job has no original image to edit')
        if job['job_type'] == 'shot_video' and (body.use_source_image or body.image_model_key or len(body.reference_upload_ids) > 1): raise HTTPException(400, 'Videos accept one optional starting frame and use the project video engine')
        for identifier in body.reference_upload_ids:
            try: upload_reference(project_id, identifier, ROOT)
            except ValueError as exc: raise HTTPException(400, str(exc))
        if body.image_model_key:
            models, _ = query("SELECT model_key FROM model_registry WHERE model_key=%s AND model_type='image' AND backend='openai_api'", (body.image_model_key,))
            if not models: raise HTTPException(400, 'Choose a registered OpenAI image model')
        options.update(use_source_image=body.use_source_image, reference_upload_ids=body.reference_upload_ids, image_model_key=body.image_model_key)
        pause_for_change(project_id)
        write_options(project_id, job['output_path'], options, ROOT)
        return options

@app.put('/projects/{project_id}/jobs/{job_id}/prompt')
def edit_job_prompt(project_id: str,job_id: int,body: PromptEdit):
    project(project_id)
    with lock:
        if active or (read_settings(project_id) or {}).get('automation_enabled'):raise HTTPException(409,'Pause production and wait for the current worker before editing prompts')
        rows,_=query('SELECT status,prompt FROM jobs WHERE project_id=%s AND id=%s',(project_id,job_id))
        if not rows or rows[0]['status'] not in {'queued','failed'}:raise HTTPException(409,'This job is already running or completed. Create an edited version from its asset; acceptance does not lock editing.')
        if rows[0]['prompt']==body.prompt:return {'saved':True,'unchanged':True}
        _,changed=query("UPDATE jobs SET prompt=%s WHERE project_id=%s AND id=%s AND status IN ('queued','failed')",(body.prompt,project_id,job_id))
        if changed!=1:raise HTTPException(409,'Job status changed. Refresh its status before editing')
        pause_for_change(project_id)
    return {'saved':True}

@app.put('/projects/{project_id}/shots/{shot_id}/video-prompt')
def edit_video_prompt(project_id: str,shot_id: str,body: PromptEdit):
    project(project_id)
    with lock:
        if active or (read_settings(project_id) or {}).get('automation_enabled'):raise HTTPException(409,'Pause production and wait for the current worker before editing prompts')
        running,_=query("SELECT id FROM jobs WHERE project_id=%s AND shot_id=%s AND job_type='shot_video' AND status IN ('running','done')",(project_id,shot_id))
        if running:raise HTTPException(409,'Video already generated. Reject it and edit the replacement job prompt.')
        rows,_=query('SELECT s.video_prompt FROM shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id WHERE sp.project_id=%s AND s.shot_id=%s',(project_id,shot_id))
        if not rows:raise HTTPException(404,'Shot missing')
        query('UPDATE shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id SET s.video_prompt=%s WHERE sp.project_id=%s AND s.shot_id=%s',(body.prompt,project_id,shot_id))
        query("UPDATE jobs SET prompt=%s WHERE project_id=%s AND shot_id=%s AND job_type='shot_video' AND status IN ('queued','failed')",(body.prompt,project_id,shot_id))
        pause_for_change(project_id)
    return {'saved':True}

@app.post('/prompts/improve')
def improve_prompt(body: ImprovePrompt):
    with lock:
        if active:raise HTTPException(409,'Wait for the current worker before using the local prompt assistant')
        host=os.getenv('OLLAMA_HOST','ollama:11434').rstrip('/')
        url=(host if host.startswith(('http://','https://')) else 'http://'+host)+'/api/generate'
        try:
            response=requests.post(url,json={'model':os.getenv('MODEL_NAME','glm-film-director'),'system':'You edit filmmaking prompts. Preserve IDs, counts, durations, stage instructions and established facts. Return only a JSON object with one string key: prompt.','prompt':f'Instruction: {body.instruction}\nOriginal prompt:\n{body.prompt}','format':'json','stream':False},timeout=180)
            response.raise_for_status();result=json.loads(response.json()['response'])
            value=PromptEdit(prompt=result['prompt'])
            return {'prompt':value.prompt}
        except (requests.RequestException,ValueError,KeyError):raise HTTPException(502,'Local prompt assistant failed. Your original prompt has not changed.')

@app.post('/projects/{project_id}/automation')
def set_automation(project_id: str,body: AutomationOptions):
    project(project_id)
    with lock:
        settings=read_settings(project_id)
        if not settings:
            if not body.enabled:return {'enabled':False,'message':'Production is paused.'}
            plan,_=query('SELECT COUNT(*) AS count FROM shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id WHERE sp.project_id=%s',(project_id,))
            if not plan or not plan[0]['count']:raise HTTPException(409,'This existing project has no shot plan to produce')
            if not body.approve_prompts:raise HTTPException(409,'Review and approve the existing prompts first')
            episodes,_=query('SELECT DISTINCT episode_id FROM scene_plan WHERE project_id=%s',(project_id,))
            settings={'legacy':True,'planning_status':'ready','completed':[],'prompts_approved':False,'spec':{'review_mode':'every_stage','episodes':len(episodes),'scenes_per_episode':0}}
        if body.enabled and settings.get('planning_status')!='ready':raise HTTPException(409,'Finish planning first')
        if body.enabled and not (body.approve_prompts or settings.get('prompts_approved')):raise HTTPException(409,'Review and approve the prompts before starting production')
        settings['automation_enabled']=body.enabled
        if body.approve_prompts:settings['prompts_approved']=True
        write_settings(project_id,settings)
    return {'enabled':body.enabled,'message':'Automatic production will continue between reviews.' if body.enabled else 'Paused. The current batch will finish.'}

@app.post('/automation/tick')
def automation_tick():
    if active:return {'status':'busy'}
    rows,_=query('SELECT project_id FROM projects ORDER BY project_id')
    for row in rows:
        settings=read_settings(row['project_id'])
        if not settings or not settings.get('automation_enabled'):continue
        dashboard=production_dashboard(row['project_id'])
        if dashboard['next_action']=='continue':
            try:return run_worker(row['project_id'],'pipeline',RunOptions(limit=1))
            except HTTPException as exc:
                if exc.status_code==409:return {'status':'busy'}
                raise
    return {'status':'waiting','message':'No production is ready to advance.'}

scheduler_stop=threading.Event()

@app.on_event('startup')
def start_scheduler():
    scheduler_stop.clear()
    def scheduler():
        while not scheduler_stop.wait(10):
            try:automation_tick()
            except Exception:continue  # DB outages remain visible through /health and dashboard.
    threading.Thread(target=scheduler,daemon=True).start()

@app.on_event('shutdown')
def stop_scheduler():
    scheduler_stop.set()


@app.get('/projects/{project_id}/story')
def production_story(project_id: str):
    project(project_id)
    bible,_=query('SELECT title,logline,premise,visual_style FROM projects WHERE project_id=%s',(project_id,))
    cast,_=query('SELECT character_id,name,role,appearance FROM characters WHERE project_id=%s ORDER BY character_id',(project_id,))
    outline,_=query('SELECT episode_number,title,summary FROM episode_outline WHERE project_id=%s ORDER BY episode_number',(project_id,))
    return {'bible':bible[0] if bible else {},'cast':cast,'outline':outline}

def ollama_url():
    host=os.getenv('OLLAMA_HOST','ollama:11434').rstrip('/')
    return host if host.startswith(('http://','https://')) else 'http://'+host

@app.get('/runtime')
def runtime_status():
    result={'worker_busy':active,'ollama':{'connected':False},'comfyui':{'connected':False}}
    try:
        response=requests.get(ollama_url()+'/api/ps',timeout=3);response.raise_for_status()
        result['ollama']={'connected':True,'models':[{'name':row.get('name') or row.get('model'),'size_vram':row.get('size_vram',0)} for row in response.json().get('models',[])]}
    except (requests.RequestException,ValueError):pass
    try:
        response=requests.get(COMFYUI_URL+'/queue',timeout=3);response.raise_for_status();data=response.json()
        if not isinstance(data.get('queue_running'),list) or not isinstance(data.get('queue_pending'),list):raise ValueError('Queue unavailable')
        result['comfyui']={'connected':True,'running':len(data['queue_running']),'pending':len(data['queue_pending'])}
    except (requests.RequestException,ValueError):pass
    return result

@app.post('/runtime/release')
def release_idle_models():
    with lock:
        if active:raise HTTPException(409,'Wait for the gateway worker to finish before releasing models')
        running,_=query("SELECT id FROM jobs WHERE status='running' LIMIT 1")
        if running:raise HTTPException(409,'Resolve running job records before releasing models')
        state=runtime_status();comfy=state['comfyui']
        if not comfy['connected']:raise HTTPException(409,'Cannot verify the ComfyUI queue. Start/connect ComfyUI before releasing shared GPU models')
        if comfy['running'] or comfy['pending']:raise HTTPException(409,'ComfyUI still has running or pending work. No models were released')
        released=[];errors=[]
        if state['ollama']['connected']:
            for model in state['ollama']['models']:
                try:
                    response=requests.post(ollama_url()+'/api/generate',json={'model':model['name'],'keep_alive':0,'stream':False},timeout=30);response.raise_for_status();released.append(model['name'])
                except requests.RequestException:errors.append('Could not unload '+str(model['name']))
        else:errors.append('Ollama unavailable; its loaded models could not be checked')
        try:
            response=requests.post(COMFYUI_URL+'/free',json={'unload_models':True,'free_memory':True},timeout=10);response.raise_for_status()
        except requests.RequestException:errors.append('ComfyUI memory release failed')
        return {'released_models':released,'errors':errors,'message':'Idle model release requested. Memory may take a moment to fall; the next local task reloads its model.'}
