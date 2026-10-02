import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_comfyui_video_jobs as video
import automation_api as api
from video_direction import Direction
from fastapi.testclient import TestClient

class VideoRecoveryTests(unittest.TestCase):
    def setUp(self):
        # These tests exercise submission recovery, not ffmpeg or direction providers.
        self.patches=[patch.object(video,'dialogue_rows',return_value=[]),patch.object(video,'quality_report',return_value={'warnings':[]}),patch.object(video,'apply_audio'),patch.object(video,'read_direction',return_value=Direction())]
        for value in self.patches:value.start();self.addCleanup(value.stop)
    def test_legacy_ltx_renders_restored_mixed_durations(self):
        with tempfile.TemporaryDirectory() as folder:
            template=Path(folder)/'workflow.json';template.write_text('{}')
            conn=Mock();cur=conn.cursor.return_value;cur.rowcount=1
            cur.fetchone.return_value={'model_key':'ltx-2.5','workflow_template_path':str(template),'locked_duration_seconds':6}
            cur.fetchall.return_value=[dict(id=i,shot_id=f'shot_{i}',duration_seconds=seconds,prompt='Action',video_prompt='Action',output_path=str(Path(folder)/f'{i}.mp4'),frame='frame.png',asset_id=i+10) for i,seconds in [(1,6),(2,8)]]
            settings={'legacy':True,'spec':{'video_model_key':'ltx-2.5'},'ltx_original_timing':{'shots':{'shot_1':6,'shot_2':8}}}
            with patch.object(video.sys,'argv',['worker','film','--limit','2']),patch.object(video.mysql.connector,'connect',return_value=conn),patch.object(video,'read_settings',return_value=settings),patch.object(video,'read_options',return_value={}),patch.object(video,'render_video') as render,patch.object(video,'record_video'),patch.object(video,'release_video_memory',return_value=True):video.main()
            self.assertEqual([call.args[3] for call in render.call_args_list],[6,8])
    def test_submission_rejection_reports_node_details_and_preserves_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            frame=Path(folder)/'frame.png';frame.write_bytes(b'image');output=Path(folder)/'clip.mp4'
            uploaded=self.response({'name':'frame.png'})
            rejected=self.response();rejected.status_code=400;rejected.text='{"node_errors":{"393":{"errors":[{"message":"Value not in list: clip_name"}]}}}'
            rejected.raise_for_status.side_effect=video.requests.HTTPError('400 Bad Request')
            with patch.object(video,'submission_queue_status',return_value='absent'),patch.object(video,'patch_video',return_value={}),patch.object(video.requests,'post',side_effect=[uploaded,rejected]) as post:
                with self.assertRaisesRegex(RuntimeError,'Value not in list: clip_name'):video.render_video(None,frame,'Action',6,output,81)
            receipt=json.loads(Path(str(output)+'.comfy.json').read_text())
            self.assertEqual(receipt['phase'],'rejected');self.assertEqual(receipt['rejection'],rejected.text);self.assertNotIn('prompt_id',receipt);self.assertEqual(post.call_count,2)
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
    def test_batch_stops_after_render_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            template=Path(folder)/'workflow.json';template.write_text('{}')
            conn=Mock();cur=conn.cursor.return_value;cur.rowcount=1
            cur.fetchone.return_value={'model_key':'ltx','workflow_template_path':str(template),'locked_duration_seconds':6}
            cur.fetchall.return_value=[dict(id=i,shot_id=f'shot_{i}',duration_seconds=6,prompt='Action',video_prompt='Action',output_path=str(Path(folder)/f'{i}.mp4'),frame='frame.png',asset_id=i+10) for i in (1,2)]
            with patch.object(video.sys,'argv',['worker','film','--limit','2']),patch.object(video.mysql.connector,'connect',return_value=conn),patch.object(video,'read_settings',return_value=None),patch.object(video,'read_options',return_value={}),patch.object(video,'render_video',side_effect=RuntimeError('CUDA out of memory')) as render:
                with self.assertRaises(SystemExit):video.main()
            self.assertEqual(render.call_count,1)
    def test_ltx_text_encoder_uses_cpu(self):
        workflow=video.patch_video(Path(__file__).resolve().parents[3]/'comfyworkflow'/'IMG-video_ltx2_5_i2v.json','frame.png','Action',6,'test')
        self.assertEqual(workflow['398:393']['inputs']['device'],'cpu')
    def test_atomic_submission_and_verified_download_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(video.requests,'get',return_value=self.response()):
            output=Path(folder)/'clip.mp4';video.save_submission(output,prompt_id='saved',phase='submitted')
            video.completed_video({'outputs':{'75':{'images':[{'filename':'clip.mp4'}]}}},output)
            self.assertTrue(video.has_saved_video(output));output.write_bytes(b'changed');self.assertFalse(video.has_saved_video(output))
    def test_unknown_post_result_is_not_submitted_again(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'clip.mp4';video.save_submission(output,phase='submitting',client_id='client')
            with patch.object(video.requests,'post') as post:
                with self.assertRaisesRegex(RuntimeError,'outcome unknown'):video.render_video(None,None,'Action',6,output,1)
            post.assert_not_called()
    def test_worker_rejects_busy_queue_before_upload(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(video,'submission_queue_status',return_value='other_work'),patch.object(video.requests,'post') as post:
            with self.assertRaisesRegex(RuntimeError,'queue is occupied'):video.render_video(None,None,'Action',6,Path(folder)/'clip.mp4',1)
            post.assert_not_called()
    def test_transient_connection_failure_retries_only_status_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'clip.mp4';video.save_submission(output,prompt_id='saved')
            record={'saved':{'outputs':{'75':{'images':[{'filename':'clip.mp4'}]}}}}
            with patch.object(video.requests,'get',side_effect=[video.requests.ConnectionError('Disconnected'),self.response(record),self.response()]),patch.object(video.requests,'post') as post,patch.object(video.time,'sleep'):
                video.render_video(None,None,'Action',6,output,1)
            post.assert_not_called();self.assertTrue(video.has_saved_video(output))
    def test_rejected_submission_is_safe_to_archive_for_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'clip.mp4';video.save_submission(output,phase='rejected',client_id='client',rejection='Invalid model input')
            identifier,record=video.saved_video(output)
            self.assertEqual(identifier,'client');self.assertEqual(record['status']['status_str'],'error')
    def test_missing_submission_stops_waiting_without_resubmitting(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(video.os.environ,{'COMFYUI_MISSING_GRACE_SECONDS':'0'}),patch.object(video.requests,'get',return_value=self.response({})),patch.object(video,'submission_queue_status',return_value='absent'),patch.object(video.requests,'post') as post:
            output=Path(folder)/'clip.mp4';video.save_submission(output,prompt_id='saved')
            with self.assertRaisesRegex(RuntimeError,'disappeared'):video.render_video(None,None,'Action',6,output,1)
            post.assert_not_called()
    def test_reset_lost_submission_archives_and_requeues(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(api,'ROOT',Path(folder)),patch.object(api,'active',False),patch.object(api,'read_settings',return_value=None),patch.object(api,'submission_queue_status',return_value='absent'):
            output=Path(folder)/'film'/'clip.mp4';video.save_submission(output,prompt_id='saved')
            with patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'output_path':str(output)}],1),([],1)]),patch.object(api,'saved_video',return_value=('saved',None)):
                result=TestClient(api.app).post('/projects/film/jobs/1/reset-lost-video',json={'prompt_id':'saved','confirm':True})
            self.assertEqual(result.status_code,200);self.assertEqual(result.json()['status'],'queued');self.assertFalse(Path(str(output)+'.comfy.json').exists());self.assertEqual(len(list(output.parent.glob('*.lost-*'))),1)
    def test_reset_refuses_existing_comfy_work(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(api,'ROOT',Path(folder)),patch.object(api,'active',False),patch.object(api,'submission_queue_status',return_value='queued'):
            output=Path(folder)/'film'/'clip.mp4';video.save_submission(output,prompt_id='saved')
            with patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'output_path':str(output)}],1)]),patch.object(api,'saved_video',return_value=('saved',None)):
                result=TestClient(api.app).post('/projects/film/jobs/1/reset-lost-video',json={'prompt_id':'saved','confirm':True})
            self.assertEqual(result.status_code,409);self.assertTrue(Path(str(output)+'.comfy.json').exists())
    def test_recovery_does_not_reset_unknown_submission(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(api,'ROOT',Path(folder)),patch.object(api,'active',False),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'id':1,'shot_id':'shot','output_path':str(Path(folder)/'film'/'clip.mp4'),'model_used':None}],1)]) as query,patch.object(api,'saved_video',return_value=('saved',None)),patch.object(api,'submission_queue_status',return_value='rendering'):
            result=TestClient(api.app).post('/projects/film/jobs/1/recover-video',json={})
            self.assertEqual(result.status_code,200);self.assertEqual(result.json()['status'],'waiting');self.assertEqual(query.call_count,2)
    def test_recovery_completes_job_without_render_submission(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(api,'ROOT',Path(folder)),patch.object(api,'active',False),patch.object(api,'query',side_effect=[([{'project_id':'film'}],1),([{'id':1,'shot_id':'shot','output_path':str(Path(folder)/'film'/'clip.mp4'),'model_used':'ltx'}],1)]),patch.object(api,'saved_video',return_value=('saved',{'status':{'completed':True}})),patch.object(api,'completed_video',return_value=True),patch.object(api,'read_options',return_value={}),patch.object(api.mysql.connector,'connect') as connect,patch.object(api,'record_video') as record:
            connect.return_value.cursor.return_value.fetchone.return_value={'asset_id':2}
            result=TestClient(api.app).post('/projects/film/jobs/1/recover-video',json={})
            self.assertEqual(result.status_code,200);self.assertEqual(result.json()['status'],'done');record.assert_called_once();connect.return_value.commit.assert_called_once()
