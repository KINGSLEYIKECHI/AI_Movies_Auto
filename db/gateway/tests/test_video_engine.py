import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import automation_api as api
from fastapi.testclient import TestClient

class VideoEngineTests(unittest.TestCase):
    def test_switch_updates_config_and_pauses_without_touching_clips(self):
        with tempfile.TemporaryDirectory() as folder:
            template=Path(folder)/'workflow.json';template.write_text('{}')
            settings={'automation_enabled':True,'prompts_approved':True,'spec':{'video_model_key':'ltx-2.5','lora_mode':'custom','lora_name':'ltx.safetensors','episodes':4}}
            responses=[([{'project_id':'film'}],1),([],0),([{'model_key':'minimax-h3','workflow_template_path':str(template),'valid_durations':'[6, 8]','available':True}],1),([],0),([{'video_model_key':'ltx-2.5','locked_duration_seconds':6}],1),([{'duration':6}],1)]
            with patch.object(api,'active',False),patch.object(api,'query',side_effect=responses),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':0,'pending':0}}),patch.object(api,'read_settings',return_value=settings),patch.object(api,'write_settings') as save,patch.object(api.mysql.connector,'connect') as connect:
                result=TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':'minimax-h3'})
            self.assertEqual(result.status_code,200)
            sql,params=connect.return_value.cursor.return_value.execute.call_args.args
            self.assertEqual(params,('minimax-h3','film'));self.assertTrue(sql.startswith('UPDATE project_model_config SET video_model_key='))
            updated=save.call_args.args[1];self.assertFalse(updated['automation_enabled']);self.assertTrue(updated['prompts_approved']);self.assertEqual(updated['spec']['lora_mode'],'none');self.assertEqual(updated['spec']['episodes'],4)
    def test_switch_blocked_with_running_job(self):
        with patch.object(api,'active',False),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'id':81}],1)]),patch.object(api.mysql.connector,'connect') as connect:
            result=TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':'minimax-h3'})
        self.assertEqual(result.status_code,409);connect.assert_not_called()
    def test_switch_blocked_with_comfy_queue(self):
        with patch.object(api,'active',False),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([],0)]),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':0,'pending':1}}),patch.object(api.mysql.connector,'connect') as connect:
            result=TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':'ltx-2.5'})
        self.assertEqual(result.status_code,409);connect.assert_not_called()
    def test_unavailable_model_cannot_be_selected(self):
        with patch.object(api,'active',False),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([],0),([{'available':False}],1)]),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':0,'pending':0}}):
            result=TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':'minimax-h3'})
        self.assertEqual(result.status_code,400)
    def test_unsupported_engine_is_rejected(self):
        self.assertEqual(TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':'other'}).status_code,422)
    def test_unsupported_shot_duration_preserves_current_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            template=Path(folder)/'workflow.json';template.write_text('{}')
            responses=[([{'project_id':'film'}],1),([],0),([{'workflow_template_path':str(template),'valid_durations':'[6]','available':True}],1),([],0),([{'video_model_key':'ltx-2.5','locked_duration_seconds':6}],1),([{'duration':8}],1)]
            with patch.object(api,'active',False),patch.object(api,'query',side_effect=responses),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':0,'pending':0}}),patch.object(api.mysql.connector,'connect') as connect:
                result=TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':'minimax-h3'})
            self.assertEqual(result.status_code,400);connect.assert_not_called()
