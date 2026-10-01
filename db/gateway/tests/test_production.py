"""Focused tests for production budgets, user control and local planning."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from production_control import ProductionSpec,budget,preview,write_settings,read_settings,can_finish_stage_before_review
from plan_production import validate_stage,stage_call
from run_comfyui_video_jobs import patch_video
import automation_api as api
from fastapi.testclient import TestClient

SPEC=dict(project_id='test_film',title='A test film',concept='A mechanic discovers a mysterious machine.',image_model_key='test-image',episodes=2,scenes_per_episode=3,shots_per_scene=5,episode_seconds=100)

class ProductionTests(unittest.TestCase):
 def test_planner_sends_current_system_contract_to_ollama(self):
  import plan_production as planner
  response=MagicMock();response.json.return_value={'response':'{"stage":"SERIES_OUTLINE"}'}
  with patch.object(planner.requests,'post',return_value=response) as post:
   self.assertEqual(planner.generate('Requested production')['stage'],'SERIES_OUTLINE')
   body=post.call_args.kwargs['json']
   self.assertIn('requested',body['system'].lower())
   self.assertNotIn('Plans EXACTLY 4',body['system'])
   self.assertEqual(body['prompt'],'Requested production')
 def test_budget_matches_requested_runtime_with_variable_shots(self):
  spec=ProductionSpec(**SPEC)
  self.assertEqual(sum(budget(spec)),100);self.assertEqual(len(budget(spec)),15)
  self.assertEqual(set(budget(spec)),{6,7})
  self.assertEqual(preview(spec)['total_shots'],30)
 def test_unrenderable_budget_is_rejected_before_generation(self):
  with self.assertRaises(ValueError):ProductionSpec(**{**SPEC,'episode_seconds':10})
 def test_ltx_cannot_use_minimax_template_lora(self):
  with self.assertRaises(ValueError):ProductionSpec(**{**SPEC,'lora_mode':'template'})
 def test_planner_rejects_wrong_counts(self):
  spec=ProductionSpec(**SPEC)
  with self.assertRaises(ValueError):validate_stage({'stage':'SCENE_PLAN','scene_plan':[]},'SCENE_PLAN',spec,1)
 def test_planner_rejects_hallucinated_shot_duration(self):
  spec=ProductionSpec(**{**SPEC,'scenes_per_episode':1,'shots_per_scene':2,'episode_seconds':10})
  data={'stage':'SHOT_PLAN','scene_ending_state':'The door closes.','shots':[{'shot_id':f'EP_001_SC_01_SH_{i:02d}','scene_id':'EP_001_SC_01','duration_seconds':6,'image_prompt':'Frame','video_prompt':'Action'} for i in (1,2)]}
  with self.assertRaises(ValueError):validate_stage(data,'SHOT_PLAN',spec,1,1)
 def test_invalid_preview_has_no_db_or_provider_side_effect(self):
  client=TestClient(api.app)
  with patch.object(api,'query') as query,patch.object(api.requests,'post') as provider:
   result=client.post('/productions/preview',json={**SPEC,'episode_seconds':5})
   self.assertEqual(result.status_code,422);query.assert_not_called();provider.assert_not_called()
 def test_prompt_edit_does_not_modify_rendered_jobs(self):
  client=TestClient(api.app)
  with patch.object(api,'query',side_effect=[([{'project_id':'test_film'}],1),([],0)]):
   self.assertEqual(client.put('/projects/test_film/jobs/1/prompt',json={'prompt':'New prompt'}).status_code,409)
 def test_auto_mode_requires_explicit_prompt_approval(self):
  client=TestClient(api.app)
  with patch.object(api,'query',return_value=([{'project_id':'test_film'}],1)),patch.object(api,'read_settings',return_value={'planning_status':'ready','prompts_approved':False}):
   self.assertEqual(client.post('/projects/test_film/automation',json={'enabled':True}).status_code,409)
 def test_planning_checkpoint_does_not_regenerate_completed_stage(self):
  spec=ProductionSpec(**SPEC)
  with patch('plan_production.read_settings',return_value={'completed':['outline']}),patch('plan_production.generate') as generate:
   stage_call(spec,'outline','SERIES_OUTLINE','Prompt');generate.assert_not_called()
 def test_stage_review_waits_after_all_renders_of_that_stage(self):
  self.assertTrue(can_finish_stage_before_review(['character_reference'],['location_reference','shot_image']))
  self.assertFalse(can_finish_stage_before_review(['character_reference'],['shot_image']))
  self.assertTrue(can_finish_stage_before_review(['shot_image'],['shot_image']))
  self.assertFalse(can_finish_stage_before_review(['shot_image'],['shot_video']))
 def test_ltx_lora_is_connected_to_model_consumers(self):
  root=Path(__file__).resolve().parents[3]/'comfyworkflow'
  workflow=patch_video(root/'IMG-video_ltx2_5_i2v.json','frame.png','Action',6,'film/test',{'lora_mode':'custom','lora_name':'compatible.safetensors','lora_strength':.7})
  self.assertEqual(workflow['automation:lora']['inputs']['model'],['398:384',0])
  self.assertEqual(workflow['398:388']['inputs']['model'],['automation:lora',0])
  self.assertEqual(workflow['398:391']['inputs']['model'],['automation:lora',0])
 def test_minimax_lora_toggle_and_strength(self):
  root=Path(__file__).resolve().parents[3]/'comfyworkflow'
  workflow=patch_video(root/'video_minimax_h3_i2v.json','frame.png','Action',6,'film/test',{'lora_mode':'custom','lora_name':'compatible.safetensors','lora_strength':.6})
  self.assertTrue(workflow['105:126']['inputs']['value']);self.assertEqual(workflow['105:121']['inputs']['strength_model'],.6)


class PlanningIntegrationTests(unittest.TestCase):
 def test_complete_custom_plan_and_checkpoint_resume(self):
  import os,re,json
  import plan_production as planner
  spec=ProductionSpec(**SPEC)
  payloads=[]
  def model(prompt):
   if 'Use stage "SERIES_OUTLINE"' in prompt:
    return {'stage':'SERIES_OUTLINE','project':{'project_id':'model_chose_wrong_id','title':'Title'},'wardrobe':[],'props':[],'characters':[{'character_id':f'CHAR_{i:03d}'} for i in range(1,4)],'locations':[{'location_id':f'LOC_{i:03d}'} for i in range(1,3)],'episode_outline':[{'episode_id':f'EP_{i:03d}','episode_number':i} for i in range(1,3)]}
   ep=int(re.search(r'ep=(\d+)',prompt)[1])
   if 'SCENE_PLAN' in prompt:
    return {'stage':'SCENE_PLAN','scene_plan':[{'scene_id':f'EP_{ep:03d}_SC_{i:02d}','episode_id':f'EP_{ep:03d}','scene_number':i,'character_ids':['CHAR_001'],'location_id':'LOC_001'} for i in range(1,4)]}
   sc=int(re.search(r'scene=(\d+)',prompt)[1]);durations=budget(spec)[(sc-1)*5:sc*5]
   return {'stage':'SHOT_PLAN','scene_ending_state':'Continuity state','shots':[{'shot_id':f'EP_{ep:03d}_SC_{sc:02d}_SH_{i+1:02d}','scene_id':f'EP_{ep:03d}_SC_{sc:02d}','duration_seconds':duration,'character_ids':['CHAR_001'],'image_prompt':'Preserve character identity','video_prompt':'Camera movement and action'} for i,duration in enumerate(durations)]}
  def load(args,**kwargs):
   if args[1].endswith('load_story_to_db.py'):payloads.append(json.loads(Path(args[2]).read_text(encoding='utf-8')))
   return MagicMock(returncode=0)
  with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'PROJECTS_BASE_PATH':folder}),patch.object(sys,'argv',['plan_production.py','test_film']),patch.object(planner.mysql.connector,'connect',return_value=MagicMock()),patch.object(planner,'generate',side_effect=model) as generate,patch.object(planner,'scene_prompt',side_effect=lambda project,ep,count:f'SCENE_PLAN ep={ep} count={count}'),patch.object(planner,'shot_prompt',side_effect=lambda project,ep,sc,count,durations:f'SHOT_PLAN ep={ep} scene={sc} count={count}'),patch.object(planner.subprocess,'run',side_effect=load):
   write_settings('test_film',{'spec':spec.model_dump(),'completed':[],'planning_status':'pending','automation_enabled':False})
   planner.main()
   settings=read_settings('test_film')
   self.assertEqual(settings['planning_status'],'ready');self.assertFalse(settings['prompts_approved'])
   self.assertEqual(generate.call_count,9);self.assertEqual(len(settings['completed']),9)
   self.assertEqual(payloads[0]['project']['project_id'],'test_film')
   all_shots=[shot for payload in payloads for shot in payload.get('shots',[])]
   self.assertEqual(len(all_shots),30);self.assertEqual(sum(shot['duration_seconds'] for shot in all_shots),200)
   planner.main();self.assertEqual(generate.call_count,9)

if __name__=='__main__':unittest.main()
