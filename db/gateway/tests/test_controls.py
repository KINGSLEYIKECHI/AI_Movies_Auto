import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import automation_api as api
from fastapi.testclient import TestClient
from run_comfyui_video_jobs import patch_video

class ControlTests(unittest.TestCase):
 def test_video_queue_requires_reviewed_images(self):
  import enqueue_asset_jobs as queue
  from unittest.mock import MagicMock
  cursor=MagicMock()
  cursor.fetchall.side_effect=[[('shot_1',),('shot_2',)],[('shot_1',)]]
  self.assertEqual(queue.enqueue_shot_videos(cursor,'example'),0)
  self.assertFalse(any('INSERT' in call.args[0] for call in cursor.execute.call_args_list))
 def test_pipeline_pauses_before_paid_generation_at_review_gate(self):
  import advance_pipeline as pipeline
  from unittest.mock import MagicMock
  conn=MagicMock();conn.cursor.return_value.fetchone.return_value=(1,)
  with patch.object(sys,'argv',['advance_pipeline.py','example']),patch.object(pipeline.mysql.connector,'connect',return_value=conn),patch.object(pipeline,'execute') as execute:
   pipeline.main()
   execute.assert_not_called()
 def setUp(self):
  self.client=TestClient(api.app)
 def test_missing_db_returns_unavailable(self):
  import mysql.connector
  with patch.object(api,'query',side_effect=mysql.connector.Error('offline')):
   self.assertEqual(self.client.get('/health').status_code,503)
 def test_outside_project_asset_is_never_served(self):
  with patch.object(api,'query',return_value=([{'output_path':str(Path(__file__).resolve())}],1)):
   self.assertEqual(self.client.get('/projects/example/assets/1/file').status_code,404)
 def test_review_cannot_overwrite_prior_approval(self):
  with patch.object(api,'query',return_value=([],0)):
   self.assertEqual(self.client.post('/projects/example/assets/1/review',json={'status':'rejected'}).status_code,409)
 def test_invalid_batch_cannot_start_work(self):
  self.assertEqual(self.client.post('/projects/example/workers/openai-references',json={'limit':0}).status_code,422)
 def test_async_run_returns_before_worker_finishes(self):
  import threading
  started=threading.Event();finish=threading.Event()
  def worker(*args,**kwargs):
   started.set();finish.wait(5)
   return type('Result',(),{'returncode':0})()
  with tempfile.TemporaryDirectory() as tmp,patch.object(api,'RUNS',Path(tmp)),patch.object(api,'query',return_value=([{'project_id':'example'}],1)),patch.object(api.subprocess,'run',side_effect=worker):
   try:
    result=self.client.post('/projects/example/workers/openai-references',json={'limit':1})
    self.assertEqual(result.status_code,202);self.assertTrue(started.wait(1))
    self.assertEqual(self.client.post('/projects/example/workers/comfy-references',json={'limit':1}).status_code,409)
   finally:
    finish.set()
    import time
    for _ in range(100):
     if not api.active:break
     time.sleep(.01)
   self.assertEqual(self.client.get(result.json()['status_url']).json()['status'],'done')
 def test_video_template_uses_supplied_frame_and_duration(self):
  root=Path(__file__).resolve().parents[3]/'comfyworkflow'
  ltx=patch_video(root/'IMG-video_ltx2_5_i2v.json','new.png','New action',8,'film/test')
  self.assertEqual(ltx['395']['inputs']['image'],'new.png')
  self.assertEqual(ltx['398:362']['inputs']['value'],8)
  mini=patch_video(root/'video_minimax_h3_i2v.json','new.png','New action',6,'film/test')
  self.assertEqual(mini['105:104']['inputs']['prompt'],'New action')
  self.assertEqual(mini['114']['inputs']['image'],'new.png')
if __name__=='__main__':unittest.main()
