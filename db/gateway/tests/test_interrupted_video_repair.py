import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import automation_api as api
from fastapi.testclient import TestClient

class InterruptedVideoRepairTests(unittest.TestCase):
    def test_confirmation_and_idle_queue_required(self):
        for confirm, queue, expected in [(False, 'absent', 400), (True, 'rendering', 409)]:
            with patch.object(api, 'active', False), patch.object(api, 'project'), patch.object(api, 'query') as query, patch.object(api, 'submission_queue_status', return_value=queue):
                result = TestClient(api.app).post('/projects/film/resolve-video-jobs', json={'confirm': confirm})
            self.assertEqual(result.status_code, expected)
            query.assert_not_called()

    def repair(self, output, assets, sidecar=False):
        if sidecar: Path(str(output)+'.comfy.json').write_text('{}')
        with patch.object(api, 'ROOT', output.parent.parent), patch.object(api, 'active', False), patch.object(api, 'project'), patch.object(api, 'read_settings', return_value=None), patch.object(api, 'submission_queue_status', return_value='absent'), patch.object(api, 'recover_video', return_value={'status':'lost'}), patch.object(api, 'query', side_effect=[([{'id':81,'shot_id':'shot','output_path':str(output)}],1),(assets,len(assets)),([],1)]) as query:
            result = TestClient(api.app).post('/projects/film/resolve-video-jobs', json={'confirm':True})
        self.assertEqual(result.status_code,200)
        return result.json(), query

    def test_registered_clip_is_preserved_and_completed(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'film'/'clip.mp4';output.parent.mkdir();output.write_bytes(b'original')
            result, query=self.repair(output,[{'id':5}])
            self.assertEqual(result['jobs'][0]['status'],'done')
            self.assertEqual(output.read_bytes(),b'original')
            self.assertIn("status='done'",query.call_args.args[0])

    def test_stale_record_without_submission_becomes_retryable(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'film'/'clip.mp4';output.parent.mkdir()
            result, query=self.repair(output,[])
            self.assertEqual(result['jobs'][0]['status'],'failed')
            self.assertIn("status='failed'",query.call_args.args[0])

    def test_unregistered_clip_and_submission_are_archived_without_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'film'/'clip.mp4';output.parent.mkdir();output.write_bytes(b'original')
            result, query=self.repair(output,[],True)
            self.assertEqual(result['jobs'][0]['status'],'failed')
            self.assertNotEqual(query.call_args.args[1][0],str(output))
            self.assertEqual(output.read_bytes(),b'original')
            self.assertFalse(Path(str(output)+'.comfy.json').exists())
            self.assertEqual(len(list(output.parent.glob('*.interrupted-*'))),1)

    def test_all_review_states_can_regenerate_video_without_overwriting_source(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'film'/'clip.mp4';output.parent.mkdir();output.write_bytes(b'original')
            paths=[]
            for status in ['approved','rejected','candidate']:
                conn=MagicMock();cur=conn.cursor.return_value;cur.lastrowid=100
                cur.fetchone.return_value={'project_id':'film','shot_id':'shot','scene_id':'scene','episode_id':'episode','job_type':'shot_video'}
                asset={'id':5,'status':status,'asset_type':'shot_video','entity_id':'shot','job_id':81,'output_path':str(output),'original_prompt':'Old action'}
                with patch.object(api,'ROOT',Path(folder)),patch.object(api,'project'),patch.object(api,'require_idle'),patch.object(api,'pause_for_change'),patch.object(api,'asset_edit_details',return_value=asset),patch.object(api.mysql.connector,'connect',return_value=conn):
                    result=TestClient(api.app).post('/projects/film/assets/5/regenerate',json={'prompt':'Improved action'})
                self.assertEqual(result.status_code,202)
                paths.append(result.json()['output_path'])
                inserted=next(c for c in cur.execute.call_args_list if c.args[0].startswith('INSERT INTO jobs'))
                self.assertEqual(inserted.args[1][8],'Improved action')
                self.assertFalse(any("SET status=" in c.args[0] for c in cur.execute.call_args_list))
            self.assertEqual(len(set(paths)),3)
            self.assertNotIn(str(output),paths)
            self.assertEqual(output.read_bytes(),b'original')
