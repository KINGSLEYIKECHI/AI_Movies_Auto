"""Render approved shot frames through the supplied local API workflows."""
import json
import hashlib
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
        workflow['398:393']['inputs']['device']=os.getenv('LTX_TEXT_ENCODER_DEVICE','cpu')
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

def save_submission(output, **updates):
    sidecar=Path(str(output)+'.comfy.json');sidecar.parent.mkdir(parents=True,exist_ok=True)
    data=json.loads(sidecar.read_text(encoding='utf-8')) if sidecar.exists() else {}
    data.update(updates);data['updated_at']=time.time()
    temporary=sidecar.with_name(sidecar.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data),encoding='utf-8');temporary.replace(sidecar)
    return data


def has_saved_video(output):
    sidecar=Path(str(output)+'.comfy.json');path=Path(output)
    if not sidecar.exists() or not path.is_file():return False
    data=json.loads(sidecar.read_text(encoding='utf-8'))
    return bool(data.get('download_sha256') and hashlib.sha256(path.read_bytes()).hexdigest()==data['download_sha256'])


def submission_queue_status(prompt_id):
    response=requests.get(COMFYUI_URL+'/queue',timeout=10);response.raise_for_status();queue=response.json()
    if not isinstance(queue.get('queue_running'),list) or not isinstance(queue.get('queue_pending'),list):raise ValueError('ComfyUI queue response unavailable')
    for key,phase in [('queue_running','rendering'),('queue_pending','queued')]:
        if any(len(item)>1 and item[1]==prompt_id for item in queue[key]):return phase
    return 'absent' if not queue['queue_running'] and not queue['queue_pending'] else 'other_work'


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
                    if Path(str(output)+'.comfy.json').exists():save_submission(output,phase='downloaded',download_sha256=hashlib.sha256(response.content).hexdigest(),artifact=artifact)
                    return True
    if status.get('completed') or status.get('status_str') == 'success':
        raise RuntimeError('ComfyUI finished but reported no downloadable video. Inspect the SaveVideo output in its history.')
    return False


def saved_video(output):
    sidecar=Path(str(output)+'.comfy.json')
    if not sidecar.is_file():raise ValueError('No saved ComfyUI submission for this job')
    data=json.loads(sidecar.read_text(encoding='utf-8'))
    if data.get('phase')=='rejected' and not data.get('prompt_id'):return data['client_id'],{'status':{'status_str':'error','messages':[data.get('rejection','Submission rejected')]}}
    if not data.get('prompt_id'):raise ValueError('Submission outcome unknown. Inspect ComfyUI queue/history before resetting; no duplicate will be submitted.')
    prompt_id=data['prompt_id']
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
    if has_saved_video(output):return
    if sidecar.is_file():
        data=json.loads(sidecar.read_text(encoding='utf-8'))
        if not data.get('prompt_id'):raise RuntimeError('Submission outcome unknown; inspect ComfyUI before retrying. No duplicate submitted.')
        prompt_id=json.loads(sidecar.read_text(encoding='utf-8'))['prompt_id']
        print(f'Resuming saved ComfyUI submission {prompt_id} for job {job_id}',flush=True)
    else:
        if submission_queue_status('')!='absent':raise RuntimeError('ComfyUI queue is occupied. Resolve existing work before submitting another video.')
        with Path(frame).open('rb') as file:
            response=requests.post(COMFYUI_URL+'/upload/image',files={'image':(f'film_{uuid.uuid4().hex}.png',file,'image/png')},data={'type':'input'},timeout=120)
        response.raise_for_status();uploaded=response.json()
        image='/'.join(x for x in [uploaded.get('subfolder'),uploaded['name']] if x)
        workflow=patch_video(template,image,prompt,seconds,f'film/job_{job_id}',lora)
        client_id=str(uuid.uuid4())
        save_submission(output,phase='submitting',client_id=client_id,workflow=workflow,job_id=job_id)
        response=requests.post(COMFYUI_URL+'/prompt',json={'prompt':workflow,'client_id':client_id},timeout=30)
        try:response.raise_for_status()
        except requests.HTTPError as exc:
            if 400<=response.status_code<500:save_submission(output,phase='rejected',rejection=response.text)
            raise RuntimeError(f'ComfyUI rejected video submission (HTTP {response.status_code}): {response.text or str(exc)}') from exc
        prompt_id=response.json()['prompt_id']
        save_submission(output,prompt_id=prompt_id,phase='submitted')
        print(f'Submitted ComfyUI prompt {prompt_id} for job {job_id}',flush=True)
    deadline=time.monotonic()+int(os.getenv('COMFYUI_TIMEOUT_SECONDS','7200'))
    missing_since=None;outage_since=None;heartbeat=0
    while time.monotonic()<deadline:
        try:
            response=requests.get(COMFYUI_URL+'/history/'+prompt_id,timeout=30);response.raise_for_status()
            record=response.json().get(prompt_id)
            if record and completed_video(record,output):return
            phase=submission_queue_status(prompt_id)
            outage_since=None
            if phase in {'absent','other_work'} and not record:
                if missing_since is None:missing_since=time.monotonic()
                if time.monotonic()-missing_since>=int(os.getenv('COMFYUI_MISSING_GRACE_SECONDS','30')):
                    raise RuntimeError('Saved video submission disappeared from ComfyUI queue and history. Check saved video render; reset the lost submission only after checking for an existing output.')
            else:missing_since=None
            if time.monotonic()>=heartbeat:
                save_submission(output,phase=phase)
                print(f'Job {job_id}: ComfyUI {phase}; prompt {prompt_id}. Waiting for a completed video.',flush=True)
                heartbeat=time.monotonic()+30
        except requests.RequestException as exc:
            if outage_since is None:outage_since=time.monotonic()
            if time.monotonic()-outage_since>=int(os.getenv('COMFYUI_NETWORK_GRACE_SECONDS','90')):
                raise RuntimeError('ComfyUI connection lost; the saved submission was preserved for recovery.') from exc
            print(f'Job {job_id}: connection interrupted; retrying status checks only.',flush=True)
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
                else:
                    original=(settings or {}).get('ltx_original_timing',{}).get('shots',{})
                    expected=original.get(job['shot_id'],model['locked_duration_seconds']) if model['model_key']=='ltx-2.5' else model['locked_duration_seconds']
                    if seconds!=expected:raise RuntimeError('Shot duration differs from the saved engine timing; correct the plan before rendering')
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
                conn.rollback();cur.execute("UPDATE jobs SET status='failed',error=%s WHERE id=%s",(str(exc),job['id']));conn.commit();failed=True;print(f"FAILED {job['id']}: {exc}",flush=True)
                print('Batch paused after this failure. Remaining video jobs stay queued.',flush=True)
                break
        print('Video batch finished. Check job status for completed and remaining clips.',flush=True)
    finally:cur.close();conn.close()
    if failed:raise SystemExit(1)
if __name__=='__main__':main()
