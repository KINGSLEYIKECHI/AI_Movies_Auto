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

def completed_video(record, output):
    """Download a reported output; never infer success from a stale local file."""
    status = record.get('status', {})
    if status.get('status_str') == 'error':
        raise RuntimeError('ComfyUI render failed: ' + str(status))
    for result in record.get('outputs', {}).values():
        for key in ('videos', 'gifs', 'images'):
            for artifact in result.get(key, []):
                if artifact.get('filename', '').lower().endswith(('.mp4', '.webm', '.mov')):
                    response = requests.get(COMFYUI_URL+'/view', params={k:artifact[k] for k in ('filename','subfolder','type') if k in artifact}, timeout=300)
                    response.raise_for_status()
                    if not response.content: raise RuntimeError('ComfyUI returned an empty video file')
                    path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=path.with_name(path.name+'.download');temporary.write_bytes(response.content);temporary.replace(path)
                    return True
    if status.get('completed') or status.get('status_str') == 'success':
        raise RuntimeError('ComfyUI finished but reported no downloadable video. Inspect the SaveVideo output in its history.')
    return False


def saved_video(output):
    sidecar=Path(str(output)+'.comfy.json')
    if not sidecar.is_file():raise ValueError('No saved ComfyUI submission for this job')
    prompt_id=json.loads(sidecar.read_text(encoding='utf-8'))['prompt_id']
    response=requests.get(COMFYUI_URL+'/history/'+prompt_id,timeout=30);response.raise_for_status()
    return prompt_id,response.json().get(prompt_id)


def release_video_memory():
    """Only unload between jobs when ComfyUI reports no work in its queue."""
    response=requests.get(COMFYUI_URL+'/queue',timeout=10);response.raise_for_status();queue=response.json()
    if not isinstance(queue.get('queue_running'),list) or not isinstance(queue.get('queue_pending'),list):
        raise RuntimeError('Cannot verify ComfyUI queue before unloading')
    if queue['queue_running'] or queue['queue_pending']:return False
    response=requests.post(COMFYUI_URL+'/free',json={'unload_models':True,'free_memory':True},timeout=10);response.raise_for_status()
    return True


def record_video(cur, project, job, model, use_uploaded_frame=False):
    cur.execute("INSERT INTO asset_records(project_id,job_id,asset_type,entity_id,output_path,generation_backend,generation_model) VALUES(%s,%s,'shot_video',%s,%s,'comfyui',%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",(project,job['id'],job['shot_id'],job['output_path'],model))
    if not use_uploaded_frame and job.get('asset_id'):
        cur.execute("INSERT IGNORE INTO job_asset_references(job_id,asset_id,reference_role) VALUES(%s,%s,'previous_shot')",(job['id'],job['asset_id']))
    cur.execute("UPDATE jobs SET status='done',error=NULL,model_used=%s WHERE id=%s AND project_id=%s",(model,job['id'],project))


def render_video(template, frame, prompt, seconds, output, job_id, lora=None):
    sidecar=Path(str(output)+'.comfy.json')
    if sidecar.is_file():
        prompt_id=json.loads(sidecar.read_text(encoding='utf-8'))['prompt_id']
        print(f'Resuming saved ComfyUI submission {prompt_id} for job {job_id}',flush=True)
    else:
        with Path(frame).open('rb') as file:
            response=requests.post(COMFYUI_URL+'/upload/image',files={'image':(f'film_{uuid.uuid4().hex}.png',file,'image/png')},data={'type':'input'},timeout=120)
        response.raise_for_status();uploaded=response.json()
        image='/'.join(x for x in [uploaded.get('subfolder'),uploaded['name']] if x)
        workflow=patch_video(template,image,prompt,seconds,f'film/job_{job_id}',lora)
        response=requests.post(COMFYUI_URL+'/prompt',json={'prompt':workflow,'client_id':str(uuid.uuid4())},timeout=30)
        response.raise_for_status();prompt_id=response.json()['prompt_id']
        sidecar.parent.mkdir(parents=True,exist_ok=True)
        sidecar.write_text(json.dumps({'prompt_id':prompt_id,'workflow':workflow}),encoding='utf-8')
        print(f'Submitted ComfyUI prompt {prompt_id} for job {job_id}',flush=True)
    deadline=time.monotonic()+int(os.getenv('COMFYUI_TIMEOUT_SECONDS','7200'))
    while time.monotonic()<deadline:
        response=requests.get(COMFYUI_URL+'/history/'+prompt_id,timeout=30);response.raise_for_status()
        record=response.json().get(prompt_id)
        if record and completed_video(record,output):return
        time.sleep(2)
    raise TimeoutError(f'ComfyUI prompt {prompt_id} unresolved. Use Check saved video render before retrying.')

def main():
    project=sys.argv[1];limit=int(sys.argv[sys.argv.index('--limit')+1]) if '--limit' in sys.argv else 1
    selected_job=int(sys.argv[sys.argv.index('--job-id')+1]) if '--job-id' in sys.argv else None
    conn=mysql.connector.connect(**DB);cur=conn.cursor(dictionary=True,buffered=True);failed=False
    try:
        cur.execute("SELECT mr.model_key,mr.workflow_template_path,pm.locked_duration_seconds FROM project_model_config pm JOIN model_registry mr ON mr.model_key=pm.video_model_key WHERE pm.project_id=%s AND mr.backend='comfyui'",(project,));model=cur.fetchone()
        if not model: raise RuntimeError('Lock a local ComfyUI video model for this project first')
        settings=read_settings(project)
        spec=ProductionSpec(**settings['spec']) if settings and not settings.get('legacy') else None
        template=Path(model['workflow_template_path'] or '')
        if not template.is_file(): raise RuntimeError('Video workflow path is missing; apply migration 13')
        cur.execute("SELECT j.*,s.duration_seconds,s.video_prompt,a.id AS asset_id,a.output_path AS frame FROM jobs j JOIN shots s ON s.shot_id=j.shot_id JOIN asset_records a ON a.project_id=j.project_id AND a.entity_id=j.shot_id AND a.asset_type='shot_image' AND a.status='approved' WHERE j.project_id=%s AND j.job_type='shot_video' AND j.status='queued' AND (%s IS NULL OR j.id=%s) AND a.id=(SELECT MAX(a2.id) FROM asset_records a2 WHERE a2.project_id=j.project_id AND a2.entity_id=j.shot_id AND a2.asset_type='shot_image' AND a2.status='approved') ORDER BY j.id LIMIT %s",(project,selected_job,selected_job,limit));jobs=cur.fetchall()
        for job in jobs:
            cur.execute("UPDATE jobs SET status='running' WHERE id=%s AND status='queued'",(job['id'],));claimed=cur.rowcount;conn.commit()
            if claimed!=1: continue
            try:
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
                record_video(cur,project,job,model['model_key'],bool(options.get('reference_upload_ids')))
                conn.commit();print(f"Created video candidate for job {job['id']}",flush=True)
                if os.getenv('RELEASE_VIDEO_MEMORY','1') != '0':
                    try:
                        released=release_video_memory()
                        print('Idle ComfyUI model unload requested.' if released else 'Memory unload skipped: ComfyUI queue still has work.',flush=True)
                    except Exception as exc:print(f'Video saved; memory unload warning: {exc}',flush=True)

            except Exception as exc:
                conn.rollback();cur.execute("UPDATE jobs SET status='failed',error=%s WHERE id=%s",(str(exc),job['id']));conn.commit();failed=True;print(f"FAILED {job['id']}: {exc}")
        print(f'Processed {len(jobs)} approved-frame video job(s). Others wait for shot approval.')
    finally:cur.close();conn.close()
    if failed:raise SystemExit(1)
if __name__=='__main__':main()
