"""Render approved shot frames through the supplied local API workflows."""
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
import requests
import mysql.connector
from run_comfyui_reference_jobs import DB, COMFYUI_URL
from production_control import ProductionSpec, budget, read_settings
from asset_control import read_options, upload_reference

def patch_video(template, image, prompt, seconds, prefix, lora=None):
    workflow = json.loads(template.read_text(encoding='utf-8'))
    if '398:376' in workflow:
        workflow['395']['inputs']['image'] = image
        workflow['398:376']['inputs']['value'] = prompt
        workflow['398:362']['inputs']['value'] = int(seconds)
        workflow['75']['inputs'].update(filename_prefix=prefix, format='mp4', codec='h264')
    elif '105:104' in workflow:
        workflow['114']['inputs']['image'] = image
        workflow['105:104']['inputs']['prompt'] = prompt
        workflow['105:111']['inputs']['value'] = float(seconds)
        workflow['92']['inputs'].update(filename_prefix=prefix, format='mp4', codec='h264')
    else: raise RuntimeError('Unsupported video workflow: use the supplied API exports')
    lora=lora or {'lora_mode':'none'}
    mode=lora.get('lora_mode','none')
    if '105:121' in workflow:
        workflow['105:126']['inputs']['value']=mode!='none'
        if mode=='custom':
            workflow['105:121']['inputs'].update(lora_name=lora['lora_name'],strength_model=lora.get('lora_strength',1))
    elif mode!='none':
        if mode!='custom':raise RuntimeError('LTX requires an explicitly selected compatible LoRA')
        for node in workflow.values():
            for key,value in node.get('inputs',{}).items():
                if value==['398:384',0]:node['inputs'][key]=['automation:lora',0]
        workflow['automation:lora']={'class_type':'LoraLoaderModelOnly','inputs':{'model':['398:384',0],'lora_name':lora['lora_name'],'strength_model':lora.get('lora_strength',1)}}
    for node in workflow.values():
        if node.get('class_type') == 'RandomNoise': node['inputs']['noise_seed'] = int.from_bytes(os.urandom(8), 'big') >> 1
    return workflow

def render_video(template, frame, prompt, seconds, output, job_id, lora=None):
    with Path(frame).open('rb') as file:
        response = requests.post(COMFYUI_URL+'/upload/image', files={'image': (f'film_{uuid.uuid4().hex}.png',file,'image/png')}, data={'type':'input'}, timeout=120)
    response.raise_for_status(); uploaded=response.json()
    image = '/'.join(x for x in [uploaded.get('subfolder'),uploaded['name']] if x)
    workflow=patch_video(template,image,prompt,seconds,f'film/job_{job_id}',lora)
    response=requests.post(COMFYUI_URL+'/prompt',json={'prompt':workflow,'client_id':str(uuid.uuid4())},timeout=30)
    response.raise_for_status(); prompt_id=response.json()['prompt_id']
    # Persist the submission before polling: an interrupted run must not be resubmitted blindly.
    sidecar=Path(str(output)+'.comfy.json'); sidecar.parent.mkdir(parents=True,exist_ok=True)
    sidecar.write_text(json.dumps({'prompt_id':prompt_id,'workflow':workflow}),encoding='utf-8')
    deadline=time.monotonic()+int(os.getenv('COMFYUI_TIMEOUT_SECONDS','7200'))
    while time.monotonic()<deadline:
        response=requests.get(COMFYUI_URL+'/history/'+prompt_id,timeout=30);response.raise_for_status()
        record=response.json().get(prompt_id)
        if record:
            if record.get('status',{}).get('status_str')=='error': raise RuntimeError(str(record['status']))
            for result in record.get('outputs',{}).values():
                for key in ('videos','gifs','images'):
                    for artifact in result.get(key,[]):
                        if artifact.get('filename','').lower().endswith(('.mp4','.webm','.mov')):
                            response=requests.get(COMFYUI_URL+'/view',params={k:artifact[k] for k in ('filename','subfolder','type') if k in artifact},timeout=300)
                            response.raise_for_status();Path(output).write_bytes(response.content);return
        time.sleep(2)
    raise TimeoutError(f'ComfyUI prompt {prompt_id} still unresolved; inspect history before retrying')

def main():
    project=sys.argv[1];limit=int(sys.argv[sys.argv.index('--limit')+1]) if '--limit' in sys.argv else 1
    selected_job=int(sys.argv[sys.argv.index('--job-id')+1]) if '--job-id' in sys.argv else None
    conn=mysql.connector.connect(**DB);cur=conn.cursor(dictionary=True);failed=False
    try:
        cur.execute("SELECT mr.model_key,mr.workflow_template_path,pm.locked_duration_seconds FROM project_model_config pm JOIN model_registry mr ON mr.model_key=pm.video_model_key WHERE pm.project_id=%s AND mr.backend='comfyui'",(project,));model=cur.fetchone()
        if not model: raise RuntimeError('Lock a local ComfyUI video model for this project first')
        settings=read_settings(project)
        spec=ProductionSpec(**settings['spec']) if settings and not settings.get('legacy') else None
        template=Path(model['workflow_template_path'] or '')
        if not template.is_file(): raise RuntimeError('Video workflow path is missing; apply migration 13')
        cur.execute("SELECT j.*,s.duration_seconds,s.video_prompt,a.id AS asset_id,a.output_path AS frame FROM jobs j JOIN shots s ON s.shot_id=j.shot_id JOIN asset_records a ON a.project_id=j.project_id AND a.entity_id=j.shot_id AND a.asset_type='shot_image' AND a.status='approved' WHERE j.project_id=%s AND j.job_type='shot_video' AND j.status='queued' AND (%s IS NULL OR j.id=%s) AND a.id=(SELECT MAX(a2.id) FROM asset_records a2 WHERE a2.project_id=j.project_id AND a2.entity_id=j.shot_id AND a2.asset_type='shot_image' AND a2.status='approved') ORDER BY j.id LIMIT %s",(project,selected_job,selected_job,limit));jobs=cur.fetchall()
        for job in jobs:
            cur.execute("UPDATE jobs SET status='running' WHERE id=%s AND status='queued'",(job['id'],));conn.commit()
            if cur.rowcount!=1: continue
            try:
                if Path(str(job['output_path'])+'.comfy.json').exists(): raise RuntimeError('Existing ComfyUI submission: inspect saved prompt history before retrying')
                seconds=job['duration_seconds'] or model['locked_duration_seconds']
                if spec:
                    match=re.search(r'_SC_(\d+)_SH_(\d+)$',job['shot_id'])
                    if not match:raise RuntimeError('Shot ID has no valid scene/shot number')
                    scene,shot=map(int,match.groups())
                    if not 1<=scene<=spec.scenes_per_episode or not 1<=shot<=spec.shots_per_scene:raise RuntimeError('Shot outside configured production')
                    expected=budget(spec)[(scene-1)*spec.shots_per_scene+shot-1]
                    if seconds!=expected:raise RuntimeError('Shot duration differs from the approved production budget')
                elif seconds!=model['locked_duration_seconds']:
                    raise RuntimeError('Shot duration differs from the project lock; correct the plan before rendering')
                prompt=re.sub(r'^\[source_image:.*?\]\s*','',job['prompt'] or job['video_prompt'])
                options = read_options(project, job['output_path'])
                frame = job['frame']
                if options.get('reference_upload_ids'):
                    _, frame = upload_reference(project, options['reference_upload_ids'][0])
                render_video(template,frame,prompt,seconds,job['output_path'],job['id'],spec.model_dump() if spec else None)
                cur.execute("INSERT INTO asset_records(project_id,job_id,asset_type,entity_id,output_path,generation_backend,generation_model) VALUES(%s,%s,'shot_video',%s,%s,'comfyui',%s)",(project,job['id'],job['shot_id'],job['output_path'],model['model_key']))
                if not options.get('reference_upload_ids'):
                    cur.execute("INSERT IGNORE INTO job_asset_references(job_id,asset_id,reference_role) VALUES(%s,%s,'previous_shot')",(job['id'],job['asset_id']))
                cur.execute("UPDATE jobs SET status='done',error=NULL,model_used=%s WHERE id=%s",(model['model_key'],job['id']));conn.commit();print(f"Created video candidate for job {job['id']}")
            except Exception as exc:
                conn.rollback();cur.execute("UPDATE jobs SET status='failed',error=%s WHERE id=%s",(str(exc),job['id']));conn.commit();failed=True;print(f"FAILED {job['id']}: {exc}")
        print(f'Processed {len(jobs)} approved-frame video job(s). Others wait for shot approval.')
    finally:cur.close();conn.close()
    if failed:raise SystemExit(1)
if __name__=='__main__':main()
