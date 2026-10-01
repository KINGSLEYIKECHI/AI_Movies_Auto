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
    settings=read_settings(project_id)
    if settings:
        settings.update(automation_enabled=False,prompts_approved=False)
        write_settings(project_id,settings)
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
    return FileResponse(Path(__file__).parent/filename)

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
    rejected,_=query("SELECT a.id FROM asset_records a WHERE a.project_id=%s AND a.status='rejected' AND a.job_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs j JOIN jobs original ON original.id=a.job_id WHERE j.project_id=a.project_id AND j.job_type=original.job_type AND j.id>original.id AND COALESCE(j.shot_id,j.character_id,j.location_id,j.prop_id)=COALESCE(original.shot_id,original.character_id,original.location_id,original.prop_id))",(project_id,))
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
    planned,_=query("SELECT s.shot_id,s.scene_id,s.video_prompt AS prompt,s.duration_seconds FROM shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id WHERE sp.project_id=%s ORDER BY sp.scene_number,s.shot_id",(project_id,))
    return {'prompts':rows,'video_prompts':planned}

@app.put('/projects/{project_id}/jobs/{job_id}/prompt')
def edit_job_prompt(project_id: str,job_id: int,body: PromptEdit):
    project(project_id)
    with lock:
        if active or (read_settings(project_id) or {}).get('automation_enabled'):raise HTTPException(409,'Pause production and wait for the current worker before editing prompts')
        _,changed=query("UPDATE jobs SET prompt=%s WHERE project_id=%s AND id=%s AND status IN ('queued','failed')",(body.prompt,project_id,job_id))
        if changed!=1:raise HTTPException(409,'Only queued or failed prompts can be edited. Reject a completed asset and create a replacement to revise it.')
    return {'saved':True}

@app.put('/projects/{project_id}/shots/{shot_id}/video-prompt')
def edit_video_prompt(project_id: str,shot_id: str,body: PromptEdit):
    project(project_id)
    with lock:
        if active or (read_settings(project_id) or {}).get('automation_enabled'):raise HTTPException(409,'Pause production and wait for the current worker before editing prompts')
        running,_=query("SELECT id FROM jobs WHERE project_id=%s AND shot_id=%s AND job_type='shot_video' AND status IN ('running','done')",(project_id,shot_id))
        if running:raise HTTPException(409,'Video already generated. Reject it and edit the replacement job prompt.')
        _,changed=query('UPDATE shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id SET s.video_prompt=%s WHERE sp.project_id=%s AND s.shot_id=%s',(body.prompt,project_id,shot_id))
        if changed!=1:raise HTTPException(404,'Shot missing or prompt unchanged')
        query("UPDATE jobs SET prompt=%s WHERE project_id=%s AND shot_id=%s AND job_type='shot_video' AND status IN ('queued','failed')",(body.prompt,project_id,shot_id))
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
        if not settings:raise HTTPException(400,'Existing projects can use Continue next stage; automatic mode requires saved production settings')
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
