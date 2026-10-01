"""Generate a full production plan locally with durable per-stage checkpoints."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import requests
import mysql.connector
from production_control import ProductionSpec,budget,outline_prompt,read_settings,write_settings,root
from run_comfyui_reference_jobs import DB
from run_glm_prompt import extract_json,STAGE_REQUIRED_KEYS
from generate_scene_plan_prompt import build_prompt as scene_prompt
from generate_shot_prompt import build_prompt as shot_prompt

HERE=Path(__file__).parent

def normalize_id(value,project):
    return value.removeprefix(project+'__') if isinstance(value,str) else value

def validate_stage(data,stage,spec,episode=None,scene=None):
    if data.get('stage')!=stage:raise ValueError(f'Expected {stage}; model returned {data.get("stage")}')
    missing=STAGE_REQUIRED_KEYS[stage]-data.keys()
    if missing:raise ValueError(f'Missing {sorted(missing)}')
    if stage=='SERIES_OUTLINE':
        for key,count,column in [('characters',spec.characters,'character_id'),('locations',spec.locations,'location_id'),('episode_outline',spec.episodes,'episode_id')]:
            rows=data[key]
            if not isinstance(rows,list) or len(rows)!=count:raise ValueError(f'{key} must have exactly {count} entries')
            ids=[row.get(column) for row in rows]
            if len(set(ids))!=count or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,28}',v) for v in ids):raise ValueError(f'Invalid or duplicate {column}')
        for index,row in enumerate(sorted(data['episode_outline'],key=lambda x:x.get('episode_number',0)),1):
            if row.get('episode_number')!=index or row['episode_id']!=f'EP_{index:03d}':raise ValueError('Episode IDs/numbers must be consecutive EP_NNN values')
        for key,column in [('wardrobe','wardrobe_id'),('props','prop_id')]:
            if not isinstance(data.get(key),list):raise ValueError(f'The outline must establish a {key} array (empty only if none are needed)')
            identifiers=[row.get(column) for row in data[key]]
            if len(set(identifiers))!=len(identifiers) or any(not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,28}',value) for value in identifiers):raise ValueError(f'Invalid {column}')
        data['project']['project_id']=spec.project_id;data['project']['title']=spec.title
        data['project']['visual_style']=spec.visual_style
        return {key:data[key] for key in ('stage','project','characters','locations','episode_outline','wardrobe','props') if key in data}
    outline_file=root(spec.project_id)/'planning'/'outline'/'response.json'
    if outline_file.is_file():
        established=json.loads(outline_file.read_text(encoding='utf-8'))
        characters={row['character_id'] for row in established['characters']}
        locations={row['location_id'] for row in established['locations']}
        for row in data.get('scene_plan',data.get('shots',[])):
            if any(normalize_id(value,spec.project_id) not in characters for value in row.get('character_ids',[])):
                raise ValueError('A scene/shot references an unestablished character')
            if row.get('location_id') and normalize_id(row['location_id'],spec.project_id) not in locations:
                raise ValueError('A scene references an unestablished location')
            for line in row.get('dialogue',[]):
                if line.get('character_id') and normalize_id(line['character_id'],spec.project_id) not in characters:
                    raise ValueError('Dialogue references an unestablished character')
    if stage=='SCENE_PLAN':
        rows=data['scene_plan'];expected={f'EP_{episode:03d}_SC_{i:02d}' for i in range(1,spec.scenes_per_episode+1)}
        if len(rows)!=len(expected) or {normalize_id(r.get('scene_id'),spec.project_id) for r in rows}!=expected:raise ValueError('Scene IDs/count do not match the production settings')
        for row in rows:
            if normalize_id(row.get('episode_id'),spec.project_id)!=f'EP_{episode:03d}' or row.get('scene_number')!=int(row['scene_id'].split('_SC_')[-1]):raise ValueError('Scene belongs to the wrong episode/number')
        return {'stage':stage,'scene_plan':rows}
    rows=data['shots'];scene_id=f'EP_{episode:03d}_SC_{scene:02d}'
    durations=budget(spec)[(scene-1)*spec.shots_per_scene:scene*spec.shots_per_scene]
    expected={f'{scene_id}_SH_{i+1:02d}':seconds for i,seconds in enumerate(durations)}
    if len(rows)!=len(expected) or {normalize_id(r.get('shot_id'),spec.project_id) for r in rows}!=set(expected):raise ValueError('Shot IDs/count do not match the production settings')
    for row in rows:
        if normalize_id(row.get('scene_id'),spec.project_id)!=scene_id:raise ValueError('Shot belongs to the wrong scene')
        if row.get('duration_seconds')!=expected[normalize_id(row['shot_id'],spec.project_id)]:raise ValueError('Shot duration does not match the runtime budget')
        if not row.get('image_prompt') or not row.get('video_prompt'):raise ValueError('Each shot requires image and video prompts')
    return {'stage':stage,'shots':rows,'scene_ending_state':data['scene_ending_state']}

def generate(prompt):
    host=os.getenv('OLLAMA_HOST','ollama:11434').rstrip('/')
    url=(host if host.startswith(('http://','https://')) else 'http://'+host)+'/api/generate'
    contract=Path(os.getenv('DIRECTOR_MODELFILE','/modelfile/Modelfile'))
    if not contract.is_file():contract=HERE.parent.parent/'Modelfile'
    match=re.search(r'SYSTEM\s+"""(.*?)"""',contract.read_text(encoding='utf-8-sig'),re.DOTALL)
    if not match:raise RuntimeError('The director Modelfile has no SYSTEM contract')
    response=requests.post(url,json={'model':os.getenv('MODEL_NAME','glm-film-director'),'system':match.group(1),'prompt':prompt,'stream':False},timeout=1800)
    response.raise_for_status()
    return json.loads(extract_json(response.json().get('response','')))

def stage_call(spec,key,stage,prompt,episode=None,scene=None):
    settings=read_settings(spec.project_id)
    if key in settings.get('completed',[]):print(f'Skipping completed {key}',flush=True);return
    settings.update(planning_stage=key,planning_status='running');write_settings(spec.project_id,settings)
    folder=root(spec.project_id)/'planning'/key;folder.mkdir(parents=True,exist_ok=True)
    prompt_file=folder/'prompt.txt';response_file=folder/'response.json'
    if not prompt_file.exists():prompt_file.write_text(prompt,encoding='utf-8')
    print(f'Planning {key}: local model is generating story data.',flush=True)
    if response_file.exists():data=json.loads(response_file.read_text(encoding='utf-8'))
    else:
        data=validate_stage(generate(prompt_file.read_text(encoding='utf-8')),stage,spec,episode,scene)
        response_file.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    # Revalidate saved data before replaying an interrupted load.
    data=validate_stage(data,stage,spec,episode,scene)
    response_file.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    subprocess.run([sys.executable,str(HERE/'load_story_to_db.py'),str(response_file),spec.project_id],check=True)
    settings=read_settings(spec.project_id);settings.setdefault('completed',[]).append(key);write_settings(spec.project_id,settings)

def main():
    project=sys.argv[1];settings=read_settings(project)
    if not settings:raise RuntimeError('Production settings not found')
    spec=ProductionSpec(**settings['spec'])
    try:
        stage_call(spec,'outline','SERIES_OUTLINE',spec.bootstrap_prompt or outline_prompt(spec))
        conn=mysql.connector.connect(**DB);cur=conn.cursor()
        try:
            cur.execute('INSERT INTO project_model_config(project_id,image_model_key,video_model_key,locked_duration_seconds) VALUES(%s,%s,%s,%s) ON DUPLICATE KEY UPDATE project_id=VALUES(project_id)',(project,spec.image_model_key,spec.video_model_key,min(budget(spec))));conn.commit()
        finally:cur.close();conn.close()
        durations=budget(spec)
        for ep in range(1,spec.episodes+1):
            stage_call(spec,f'episode_{ep:03d}_scenes','SCENE_PLAN',scene_prompt(project,ep,spec.scenes_per_episode)+f'\nProduction direction: {spec.direction}\nTarget episode runtime: {spec.episode_seconds} seconds.',ep)
            for sc in range(1,spec.scenes_per_episode+1):
                stage_call(spec,f'episode_{ep:03d}_scene_{sc:02d}_shots','SHOT_PLAN',shot_prompt(project,ep,sc,spec.shots_per_scene,durations[(sc-1)*spec.shots_per_scene:sc*spec.shots_per_scene])+f'\nVisual style: {spec.visual_style}\nVideo engine: {spec.video_model_key}\nProduction direction: {spec.direction}',ep,sc)
        for kind in ('references','shot-images'):
            subprocess.run([sys.executable,str(HERE/'enqueue_asset_jobs.py'),project,kind],check=True)
        settings=read_settings(project);settings.update(planning_status='ready',planning_stage='complete',prompts_approved=False);write_settings(project,settings)
        print('PLAN_READY: story data and editable image prompts are ready. Review prompts before starting production.',flush=True)
    except Exception as exc:
        settings=read_settings(project);settings.update(planning_status='failed',planning_error=str(exc));write_settings(project,settings);raise
if __name__=='__main__':main()
