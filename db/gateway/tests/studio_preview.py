"""Local UI fixture. No real databases or generation providers are called."""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
workspace=tempfile.TemporaryDirectory(prefix='film-studio-preview-')
os.environ['PROJECTS_BASE_PATH']=workspace.name
import automation_api as api
from production_control import ProductionSpec,write_settings,read_settings
api.RUNS=Path(workspace.name)/'runs'
projects={'preview_workshop':{'project_id':'preview_workshop','title':'The Echo Workshop'}}
spec=ProductionSpec(project_id='preview_workshop',title='The Echo Workshop',concept='A young mechanic discovers a machine that can replay fragments of the past.',image_model_key='test-image',episodes=2,scenes_per_episode=3,shots_per_scene=5,episode_seconds=100)
write_settings('preview_workshop',{'spec':spec.model_dump(),'planning_status':'ready','completed':['outline'],'automation_enabled':False,'prompts_approved':False})
models=[{'model_key':'test-image','model_type':'image','backend':'openai_api','display_name':'Configured image model (preview)','available':True},{'model_key':'ltx-2.5','model_type':'video','backend':'comfyui','display_name':'LTX 2.5','available':True},{'model_key':'minimax-h3','model_type':'video','backend':'comfyui','display_name':'MiniMax H3','available':True}]
def jobs(project):return [{'id':1,'job_type':'character_reference','scene_id':None,'shot_id':None,'status':'queued','prompt':'Canonical reference portrait of a Nigerian mechanic. Preserve natural facial detail, workshop clothing and warm cinematic lighting.'},{'id':2,'job_type':'shot_image','scene_id':project+'__EP_001_SC_01','shot_id':project+'__EP_001_SC_01_SH_01','status':'queued','prompt':'Wide establishing frame inside the workshop. The mechanic stands beside the machine. Warm late-afternoon light; grounded cinematic realism.'}]

def query(sql,params=()):
 p=params[0] if params else 'preview_workshop'
 if sql.startswith('SELECT project_id FROM projects WHERE'):return ([projects[p]] if p in projects else [],1)
 if sql.startswith('SELECT project_id FROM projects ORDER'):return (list(projects.values()),len(projects))
 if sql.startswith('INSERT INTO projects'):
  projects[params[0]]={'project_id':params[0],'title':params[1]};return [],1
 if 'FROM model_registry' in sql:return models,3
 if sql.startswith('SELECT job_type,status'):return ([{'job_type':'character_reference','status':'queued','count':1},{'job_type':'shot_image','status':'queued','count':1}],2)
 if sql.startswith('SELECT DISTINCT episode_id'):return ([{'episode_id':p+'__EP_001'}],1)
 if sql.startswith('SELECT id,job_type,scene_id'):return jobs(p),2
 if sql.startswith('SELECT s.shot_id,s.scene_id,s.video_prompt'):return ([{'shot_id':p+'__EP_001_SC_01_SH_01','scene_id':p+'__EP_001_SC_01','prompt':'Slow push-in as the mechanic touches the machine. Subtle hand motion; workshop room tone.','duration_seconds':7}],1)
 if sql.startswith('SELECT title,logline'):return ([{'title':'The Echo Workshop','logline':'A mechanic finds that the past can answer back.','premise':'A mysterious machine changes her workshop.','visual_style':'Cinematic realism'}],1)
 if sql.startswith('SELECT episode_number,title'):return ([{'episode_number':1,'title':'The First Echo','summary':'An ordinary repair becomes a discovery that changes the workshop forever.'}],1)
 if sql.startswith('UPDATE jobs SET prompt'):return [],1
 return [],0
api.query=query
api.automation_tick=lambda:{'status':'fixture','message':'Preview only; no production scheduling.'}
def stub_run(command,**kwargs):
 if 'plan_production.py' in command[2]:
  pid=command[3];settings=read_settings(pid);settings.update(planning_status='ready',completed=['outline'],prompts_approved=False);write_settings(pid,settings)
 if kwargs.get('stdout'):kwargs['stdout'].write('Fixture planning completed. No providers called.\n')
 return SimpleNamespace(returncode=0)
api.subprocess.run=stub_run
api.requests.get=lambda *args,**kwargs:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'LoraLoaderModelOnly':{'input':{'required':{'lora_name':[['ltx_compatible_preview.safetensors','minimax_compatible_preview.safetensors']]}}}})
api.requests.post=lambda *args,**kwargs:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'response':json.dumps({'prompt':kwargs['json']['prompt'].split('Original prompt:\n')[-1]+'\nUse precise camera language and grounded cinematic action.'})})
if __name__=='__main__':
 import uvicorn
 try:uvicorn.run(api.app,host='127.0.0.1',port=8765)
 finally:workspace.cleanup()
