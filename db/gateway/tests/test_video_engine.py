import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import automation_api as api
from fastapi.testclient import TestClient

class VideoEngineTests(unittest.TestCase):
    def test_legacy_round_trip_with_missing_or_minimal_setup(self):
        for initial in [None,{'legacy':True,'spec':{'review_mode':'every_stage','scenes_per_episode':0}}]:
            with self.subTest(initial=initial),tempfile.TemporaryDirectory() as folder:
                template=Path(folder)/'workflow.json';template.write_text('{}');settings=initial
                for engine,current,shots,expected in [('minimax-h3','ltx-2.5',[6,8],[10,10]),('minimax-h3','minimax-h3',[10,10],[10,10]),('ltx-2.5','minimax-h3',[10,10],[6,8])]:
                    responses=[([{'project_id':'film'}],1),([],0),([{'workflow_template_path':str(template),'valid_durations':'[6,10]' if engine=='minimax-h3' else '[6,8]','available':True}],1),([],0),([{'video_model_key':current,'locked_duration_seconds':6 if current=='ltx-2.5' else 10}],1),([{'shot_id':str(i),'duration':d} for i,d in enumerate(shots)],2)]
                    with patch.object(api,'active',False),patch.object(api,'query',side_effect=responses),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':0,'pending':0}}),patch.object(api,'read_settings',return_value=settings),patch.object(api,'write_settings') as save,patch.object(api.mysql.connector,'connect') as connect:
                        result=TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':engine})
                    self.assertEqual(result.status_code,200,result.text)
                    settings=save.call_args.args[1]
                    self.assertTrue(settings['legacy']);self.assertFalse(settings['automation_enabled'])
                    self.assertEqual(settings['ltx_original_timing']['shots'],{'0':6,'1':8})
                    updates=connect.return_value.cursor.return_value.execute.call_args_list
                    self.assertEqual([c.args[1][0] for c in updates[:-1]],expected)
                    self.assertEqual(updates[-1].args[1],(engine,6 if engine=='ltx-2.5' else 10,'film'))
    def test_failed_legacy_switch_rolls_back_and_removes_new_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            template=Path(folder)/'workflow.json';template.write_text('{}');project_dir=Path(folder)/'film';project_dir.mkdir()
            responses=[([{'project_id':'film'}],1),([],0),([{'workflow_template_path':str(template),'valid_durations':'[6,10]','available':True}],1),([],0),([{'video_model_key':'ltx-2.5','locked_duration_seconds':6}],1),([{'shot_id':'shot','duration':6}],1)]
            def save(project,data): (project_dir/'production.json').write_text(str(data))
            with patch.object(api,'active',False),patch.object(api,'query',side_effect=responses),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':0,'pending':0}}),patch.object(api,'read_settings',return_value=None),patch.object(api,'write_settings',side_effect=save),patch.object(api,'root',return_value=project_dir),patch.object(api.mysql.connector,'connect') as connect:
                connect.return_value.commit.side_effect=RuntimeError('Database failed')
                result=TestClient(api.app,raise_server_exceptions=False).put('/projects/film/video-engine',json={'video_model_key':'minimax-h3'})
            self.assertEqual(result.status_code,500);connect.return_value.rollback.assert_called_once();self.assertFalse((project_dir/'production.json').exists())
    def test_switch_round_trip_restores_original_timing_and_repeated_save_keeps_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            template=Path(folder)/'workflow.json';template.write_text('{}')
            settings={'automation_enabled':False,'spec':{'scenes_per_episode':1,'shots_per_scene':2,'episode_seconds':15}}
            for engine,current,shots,expected in [('minimax-h3','ltx-2.5',[7,8],[10,10]),('minimax-h3','minimax-h3',[10,10],[10,10]),('ltx-2.5','minimax-h3',[10,10],[7,8])]:
                responses=[([{'project_id':'film'}],1),([],0),([{'workflow_template_path':str(template),'valid_durations':'[6,10]' if engine=='minimax-h3' else '[7,8]','available':True}],1),([],0),([{'video_model_key':current,'locked_duration_seconds':7 if current=='ltx-2.5' else 10}],1),([{'shot_id':str(i),'duration':d} for i,d in enumerate(shots)],2)]
                with patch.object(api,'active',False),patch.object(api,'query',side_effect=responses),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':0,'pending':0}}),patch.object(api,'read_settings',return_value=settings),patch.object(api,'write_settings') as save,patch.object(api.mysql.connector,'connect') as connect:
                    result=TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':engine})
                self.assertEqual(result.status_code,200,result.text)
                settings=save.call_args.args[1]
                updates=connect.return_value.cursor.return_value.execute.call_args_list
                self.assertEqual([c.args[1][0] for c in updates[:-1]],expected)
                self.assertEqual(settings['ltx_original_timing']['shots'],{'0':7,'1':8})
                self.assertEqual(settings['spec']['episode_seconds'],20 if engine=='minimax-h3' else 15)
    def test_switch_updates_config_and_pauses_without_touching_clips(self):
        with tempfile.TemporaryDirectory() as folder:
            template=Path(folder)/'workflow.json';template.write_text('{}')
            settings={'automation_enabled':True,'prompts_approved':True,'spec':{'video_model_key':'ltx-2.5','lora_mode':'custom','lora_name':'ltx.safetensors','episodes':4,'scenes_per_episode':1,'shots_per_scene':1,'episode_seconds':6}}
            responses=[([{'project_id':'film'}],1),([],0),([{'model_key':'minimax-h3','workflow_template_path':str(template),'valid_durations':'[6, 10]','available':True}],1),([],0),([{'video_model_key':'ltx-2.5','locked_duration_seconds':6}],1),([{'shot_id':'shot','duration':6}],1)]
            with patch.object(api,'active',False),patch.object(api,'query',side_effect=responses),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':0,'pending':0}}),patch.object(api,'read_settings',return_value=settings),patch.object(api,'write_settings') as save,patch.object(api.mysql.connector,'connect') as connect:
                result=TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':'minimax-h3'})
            self.assertEqual(result.status_code,200)
            sql,params=connect.return_value.cursor.return_value.execute.call_args.args
            self.assertEqual(params,('minimax-h3',10,'film'));self.assertTrue(sql.startswith('UPDATE project_model_config SET video_model_key='))
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
            responses=[([{'project_id':'film'}],1),([],0),([{'workflow_template_path':str(template),'valid_durations':'[6]','available':True}],1),([],0),([{'video_model_key':'ltx-2.5','locked_duration_seconds':6}],1),([{'shot_id':'shot','duration':8}],1)]
            with patch.object(api,'active',False),patch.object(api,'query',side_effect=responses),patch.object(api,'runtime_status',return_value={'comfyui':{'connected':True,'running':0,'pending':0}}),patch.object(api,'read_settings',return_value={'spec':{'scenes_per_episode':1,'shots_per_scene':1,'episode_seconds':8}}),patch.object(api.mysql.connector,'connect') as connect:
                result=TestClient(api.app).put('/projects/film/video-engine',json={'video_model_key':'minimax-h3'})
            self.assertEqual(result.status_code,400);connect.assert_not_called()
