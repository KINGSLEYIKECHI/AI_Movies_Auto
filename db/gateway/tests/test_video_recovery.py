import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_comfyui_video_jobs as video
import automation_api as api
from fastapi.testclient import TestClient

class VideoRecoveryTests(unittest.TestCase):
    def response(self,data=None,content=b'clip'):
        response=Mock();response.json.return_value=data;response.content=content;return response
    def test_completed_video_downloads_savevideo_images(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(video.requests,'get',return_value=self.response()):
            output=Path(folder)/'clip.mp4'
            self.assertTrue(video.completed_video({'status':{'completed':True},'outputs':{'75':{'images':[{'filename':'clip.mp4','type':'output'}]}}},output))
            self.assertEqual(output.read_bytes(),b'clip')
    def test_terminal_success_without_video_fails_immediately(self):
        with self.assertRaisesRegex(RuntimeError,'no downloadable video'):
            video.completed_video({'status':{'status_str':'success','completed':True},'outputs':{}},'unused')
    def test_terminal_failure_raises(self):
        with self.assertRaisesRegex(RuntimeError,'render failed'):
            video.completed_video({'status':{'status_str':'error'}},'unused')
    def test_incomplete_history_keeps_waiting(self):
        self.assertFalse(video.completed_video({'status':{'completed':False},'outputs':{}},'unused'))
    def test_existing_submission_is_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'clip.mp4';Path(str(output)+'.comfy.json').write_text(json.dumps({'prompt_id':'saved'}))
            record={'saved':{'status':{'completed':True},'outputs':{'75':{'images':[{'filename':'clip.mp4'}]}}}}
            with patch.object(video.requests,'get',side_effect=[self.response(record),self.response()]),patch.object(video.requests,'post') as post:
                video.render_video(None,None,'Prompt',6,output,1)
            post.assert_not_called();self.assertTrue(output.is_file())
    def test_unload_is_skipped_with_pending_comfy_job(self):
        with patch.object(video.requests,'get',return_value=self.response({'queue_running':[],'queue_pending':[[1,'other']]})),patch.object(video.requests,'post') as post:
            self.assertFalse(video.release_video_memory());post.assert_not_called()
    def test_unload_requested_for_idle_queue(self):
        with patch.object(video.requests,'get',return_value=self.response({'queue_running':[],'queue_pending':[]})),patch.object(video.requests,'post',return_value=self.response()) as post:
            self.assertTrue(video.release_video_memory())
            self.assertEqual(post.call_args.kwargs['json'],{'unload_models':True,'free_memory':True})
    def test_full_run_log_is_not_truncated(self):
        import uuid
        identifier=str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as folder,patch.object(api,'RUNS',Path(folder)):
            Path(folder,identifier+'.json').write_text(json.dumps({'id':identifier,'status':'failed'}))
            Path(folder,identifier+'.log').write_text('detail '*3000)
            result=TestClient(api.app).get('/runs/'+identifier+'/log')
            self.assertEqual(result.status_code,200);self.assertGreater(len(result.text),8000)
    def test_full_run_log_rejects_invalid_id(self):
        self.assertEqual(TestClient(api.app).get('/runs/not-a-run/log').status_code,400)
    def test_batch_continues_after_memory_release_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            template=Path(folder)/'workflow.json';template.write_text('{}')
            conn=Mock();cur=conn.cursor.return_value;cur.rowcount=1
            cur.fetchone.return_value={'model_key':'ltx','workflow_template_path':str(template),'locked_duration_seconds':6}
            cur.fetchall.return_value=[dict(id=i,shot_id=f'shot_{i}',duration_seconds=6,prompt='Action',video_prompt='Action',output_path=str(Path(folder)/f'{i}.mp4'),frame='frame.png',asset_id=i+10) for i in (1,2)]
            with patch.object(video.sys,'argv',['worker','film','--limit','2']),patch.object(video.mysql.connector,'connect',return_value=conn),patch.object(video,'read_settings',return_value=None),patch.object(video,'read_options',return_value={}),patch.object(video,'render_video') as render,patch.object(video,'record_video') as record,patch.object(video,'release_video_memory',side_effect=[RuntimeError('Free failed'),True]):
                video.main()
            self.assertEqual(render.call_count,2);self.assertEqual(record.call_count,2)
    def test_recovery_does_not_reset_unknown_submission(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(api,'ROOT',Path(folder)),patch.object(api,'active',False),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'id':1,'shot_id':'shot','output_path':str(Path(folder)/'film'/'clip.mp4'),'model_used':None}],1)]) as query,patch.object(api,'saved_video',return_value=('saved',None)):
            result=TestClient(api.app).post('/projects/film/jobs/1/recover-video',json={})
            self.assertEqual(result.status_code,200);self.assertEqual(result.json()['status'],'waiting');self.assertEqual(query.call_count,2)
    def test_recovery_completes_job_without_render_submission(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(api,'ROOT',Path(folder)),patch.object(api,'active',False),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'id':1,'shot_id':'shot','output_path':str(Path(folder)/'film'/'clip.mp4'),'model_used':'ltx'}],1)]),patch.object(api,'saved_video',return_value=('saved',{'status':{'completed':True}})),patch.object(api,'completed_video',return_value=True),patch.object(api,'read_options',return_value={}),patch.object(api.mysql.connector,'connect') as connect,patch.object(api,'record_video') as record:
            connect.return_value.cursor.return_value.fetchone.return_value={'asset_id':2}
            result=TestClient(api.app).post('/projects/film/jobs/1/recover-video',json={})
            self.assertEqual(result.status_code,200);self.assertEqual(result.json()['status'],'done');record.assert_called_once();connect.return_value.commit.assert_called_once()
