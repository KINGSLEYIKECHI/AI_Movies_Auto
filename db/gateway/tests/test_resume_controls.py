"""Regressions from the production-machine queue and editing failures."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import automation_api as api
import enqueue_asset_jobs as enqueue
from fastapi.testclient import TestClient
from run_openai_reference_jobs import next_job

class UnbufferedCursor:
    """Emulate MySQL's unread-result rule with several jobs per entity."""
    def __init__(self):self.pending=[]
    def execute(self,sql,params):
        if self.pending:raise RuntimeError('Unread result found')
        self.pending=[(11,),(12,)]
        if 'LIMIT 1' in sql:self.pending=self.pending[:1]
    def fetchone(self):return self.pending.pop(0) if self.pending else None

class ResumeControlsTests(unittest.TestCase):
    def setUp(self):self.client=TestClient(api.app)
    def test_duplicate_reference_versions_do_not_leave_unread_rows(self):
        cursor=UnbufferedCursor()
        self.assertTrue(enqueue._job_exists(cursor,'film','character_reference','character_id','CHAR_1'))
        self.assertTrue(enqueue._job_exists(cursor,'film','location_reference','location_id','LOC_1'))
        self.assertEqual(cursor.pending,[])
    def test_save_unchanged_queued_prompt_succeeds_without_update(self):
        with patch.object(api,'read_settings',return_value=None),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'status':'queued','prompt':'Same prompt'}],1)]) as query:
            result=self.client.put('/projects/film/jobs/3/prompt',json={'prompt':'Same prompt'})
        self.assertEqual(result.status_code,200);self.assertTrue(result.json()['unchanged'])
        self.assertEqual(query.call_count,2)
    def test_save_changed_queued_prompt_succeeds(self):
        with patch.object(api,'read_settings',return_value=None),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'status':'queued','prompt':'Old prompt'}],1),([],1)]):
            result=self.client.put('/projects/film/jobs/3/prompt',json={'prompt':'New prompt'})
        self.assertEqual(result.status_code,200)
    def test_legacy_project_can_enable_reviewed_automation(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'PROJECTS_BASE_PATH':folder}),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'count':64}],1),([{'episode_id':'film__EP_001'}],1)]):
            result=self.client.post('/projects/film/automation',json={'enabled':True,'approve_prompts':True})
            from production_control import read_settings
            settings=read_settings('film')
        self.assertEqual(result.status_code,200);self.assertTrue(settings['legacy']);self.assertTrue(settings['prompts_approved']);self.assertEqual(settings['spec']['review_mode'],'every_stage')
    def test_legacy_project_still_requires_explicit_prompt_approval(self):
        with patch.object(api,'read_settings',return_value=None),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'count':64}],1)]):
            result=self.client.post('/projects/film/automation',json={'enabled':True})
        self.assertEqual(result.status_code,409)
    def test_stage_worker_cannot_select_other_image_types(self):
        cursor=MagicMock();cursor.fetchone.return_value=None
        next_job(cursor,'film',stage='references')
        sql,params=cursor.execute.call_args.args
        self.assertIn("job_type<>'shot_image'",sql);self.assertEqual(params[-3:],('references',)*3)
        next_job(cursor,'film',stage='shot-images')
        self.assertEqual(cursor.execute.call_args.args[1][-3:],('shot-images',)*3)
    def test_memory_release_refuses_comfy_work(self):
        with patch.object(api,'query',return_value=([],0)),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':1,'pending':0}}),patch.object(api.requests,'post') as post:
            result=self.client.post('/runtime/release',json={})
        self.assertEqual(result.status_code,409);post.assert_not_called()
    def test_memory_release_uses_only_idle_engine_endpoints(self):
        response=MagicMock()
        state={'comfyui':{'connected':True,'running':0,'pending':0},'ollama':{'connected':True,'models':[{'name':'director'}]}}
        with patch.object(api,'query',return_value=([],0)),patch.object(api,'runtime_status',return_value=state),patch.object(api.requests,'post',return_value=response) as post:
            result=self.client.post('/runtime/release',json={})
        self.assertEqual(result.status_code,200);self.assertEqual(post.call_count,2)
        self.assertEqual(post.call_args_list[0].kwargs['json']['keep_alive'],0)
        self.assertTrue(post.call_args_list[1].kwargs['json']['unload_models'])

if __name__=='__main__':unittest.main()
