import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import project_cleanup as cleanup
import automation_api as api
import video_direction as direction
from fastapi.testclient import TestClient

class VideoMaintenanceTests(unittest.TestCase):
    def connection(self,output,refs=0):
        conn=MagicMock();cur=conn.cursor.return_value
        cur.fetchone.side_effect=[{'id':7,'asset_type':'shot_video','status':'superseded','job_id':9,'output_path':str(output)}, {'status':'done'},{'n':refs},{'n':0},{'n':0}]
        return conn

    def test_delete_removes_bundle_preserves_other_versions_and_uploads(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);own=base/'film';own.mkdir();output=own/'v1.mp4'
            for name in ['v1.mp4','v1.mp4.comfy.json','v1.mp4.raw.mp4','v1.start.png','v2.mp4','reference.png']:(own/name).write_text('data')
            audio=own/'v1.mp4.audio';audio.mkdir();(audio/'voice.wav').write_text('speech')
            conn=self.connection(output);result=cleanup.delete_video_asset(conn,'film',7,base)
            self.assertTrue(result['cleanup_complete']);self.assertFalse(output.exists());self.assertFalse(audio.exists())
            self.assertTrue((own/'v2.mp4').exists());self.assertTrue((own/'reference.png').exists())
            conn.commit.assert_called_once()
            self.assertTrue(any(call.args[0].startswith('DELETE FROM jobs') for call in conn.cursor.return_value.execute.call_args_list))

    def test_referenced_video_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);output=base/'film'/'v1.mp4';output.parent.mkdir();output.write_text('keep')
            conn=self.connection(output,refs=1)
            with self.assertRaisesRegex(ValueError,'reference'):cleanup.delete_video_asset(conn,'film',7,base)
            self.assertTrue(output.exists());conn.commit.assert_not_called()

    def test_unresolved_owner_job_is_protected(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);output=base/'film'/'v1.mp4';output.parent.mkdir();output.write_text('keep')
            conn=self.connection(output);conn.cursor.return_value.fetchone.side_effect=[{'asset_type':'shot_video','job_id':9},{'status':'queued'}]
            with self.assertRaisesRegex(ValueError,'Resolve'):cleanup.delete_video_asset(conn,'film',7,base)
            self.assertTrue(output.exists());conn.commit.assert_not_called()

    def test_export_delete_cleans_unique_assembly_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);assembly=base/'film'/'final'/'episode'/('b'*32);assembly.mkdir(parents=True);output=assembly/'episode.mp4'
            for name in ['episode.mp4','episode.srt','clips.txt','0000.mp4']:(assembly/name).write_text('data')
            conn=MagicMock();cur=conn.cursor.return_value;cur.fetchone.side_effect=[{'asset_type':'final_render','status':'candidate','job_id':None,'output_path':str(output)},{'n':0},{'n':0},{'n':0}];cur.fetchall.return_value=[]
            result=cleanup.delete_video_asset(conn,'film',7,base)
            self.assertTrue(result['cleanup_complete']);self.assertFalse(list(assembly.iterdir()))

    def test_commit_failure_restores_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);output=base/'film'/'v1.mp4';output.parent.mkdir();output.write_text('keep')
            conn=self.connection(output);conn.commit.side_effect=RuntimeError('DB down')
            with self.assertRaises(RuntimeError):cleanup.delete_video_asset(conn,'film',7,base)
            self.assertEqual(output.read_text(),'keep');conn.rollback.assert_called_once()

    def test_pending_file_cleanup_retries_without_restoring_deleted_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);output=base/'film'/'v1.mp4';output.parent.mkdir();output.write_text('delete')
            with patch.object(cleanup.shutil,'rmtree',side_effect=PermissionError('locked')):
                result=cleanup.delete_video_asset(self.connection(output),'film',7,base)
            self.assertFalse(result['cleanup_complete']);self.assertFalse(output.exists())
            conn=MagicMock();conn.cursor.return_value.fetchone.return_value=None
            self.assertEqual(cleanup.recover_asset_cleanup(conn,'film',base),[])
            self.assertFalse(list((output.parent/'.asset-deleting').iterdir()))

    def test_crash_before_commit_restores_quarantined_file(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);output=base/'film'/'v1.mp4';trash=output.parent/'.asset-deleting';bundle=trash/('a'*32);bundle.mkdir(parents=True)
            (bundle/output.name).write_text('original')
            (trash/('a'*32+'.json')).write_text(json.dumps({'asset_id':7,'folder':'a'*32,'files':[{'name':output.name,'original':str(output)}]}))
            conn=MagicMock();conn.cursor.return_value.fetchone.return_value=(7,)
            cleanup.recover_asset_cleanup(conn,'film',base)
            self.assertEqual(output.read_text(),'original')

    def test_other_project_file_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);output=base/'other'/'v1.mp4';output.parent.mkdir();output.write_text('keep')
            with self.assertRaisesRegex(ValueError,'outside'):cleanup.delete_video_asset(self.connection(output),'film',7,base)
            self.assertTrue(output.exists())

    def test_delete_api_requires_confirmation_and_idle(self):
        with patch.object(api,'project'),patch.object(api,'require_idle'),patch.object(api,'delete_video_asset') as delete:
            response=TestClient(api.app).request('DELETE','/projects/film/assets/7',json={'confirm':False})
            self.assertEqual(response.status_code,400);delete.assert_not_called()
        with patch.object(api,'project'),patch.object(api,'active',True):
            response=TestClient(api.app).request('DELETE','/projects/film/assets/7',json={'confirm':True})
            self.assertEqual(response.status_code,409)

    def test_healing_known_ready_result_never_starts_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'film'/'v1.mp4';output.parent.mkdir();output.write_text('video')
            Path(str(output)+'.comfy.json').write_text(json.dumps({'phase':'ready'}));Path(str(output)+'.quality.json').write_text('{}')
            with patch.object(api,'ROOT',Path(folder)),patch.object(api,'project'),patch.object(api,'active',False),patch.object(api.mysql.connector,'connect'),patch.object(api,'recover_asset_cleanup',return_value=[]),patch.object(api,'query',return_value=([{'id':9,'output_path':str(output)}],1)),patch.object(api,'has_saved_video',return_value=True),patch.object(api,'recover_video',return_value={'status':'done'}) as recover,patch.object(api,'start_run') as start:
                result=api.heal_video_jobs('film')
                self.assertEqual(result['jobs'][0]['status'],'done');recover.assert_called_once();start.assert_not_called()

    def test_healing_unknown_submission_does_not_reset_or_recover(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(api,'ROOT',Path(folder)),patch.object(api,'project'),patch.object(api,'active',False),patch.object(api.mysql.connector,'connect'),patch.object(api,'recover_asset_cleanup',return_value=[]),patch.object(api,'query',return_value=([{'id':9,'output_path':str(Path(folder)/'film'/'v1.mp4')}],1)),patch.object(api,'recover_video') as recover,patch.object(api,'start_run') as start:
            result=api.heal_video_jobs('film');self.assertEqual(result['jobs'][0]['status'],'needs_attention');recover.assert_not_called();start.assert_not_called()

    def test_prompt_toggles_disable_only_selected_rules(self):
        config=direction.Direction(speaker_rules=False,lip_sync_prompt=False,ending_hold_prompt=False,continuity_prompt=False,extra_instructions='Camera stays still.')
        prompt=direction.compile_prompt('Open door',10,[{'character_id':'A','line':'Hello'}],config,direction.ShotDirection(),'ltx','Old action')
        for omitted in ['No overlapping voices','synchronised visible lip','Finish every spoken word','Previous shot context','Complete the single main action']:
            self.assertNotIn(omitted,prompt)
        self.assertIn('<d>[English] Hello</d>',prompt);self.assertIn('Camera stays still.',prompt)
        with self.assertRaisesRegex(ValueError,'too long'):direction.compile_prompt('',10,[{'character_id':'A','line':'word '*50}],config,direction.ShotDirection(),'ltx')

    def test_missing_config_keeps_current_prompt_defaults(self):
        config=direction.Direction();self.assertTrue(config.speaker_rules);self.assertTrue(config.lip_sync_prompt);self.assertTrue(config.auto_recover)
        self.assertIn('No overlapping voices',direction.compile_prompt('',10,[{'character_id':'A','line':'Hello'}],config,direction.ShotDirection(),'ltx'))

if __name__=='__main__':unittest.main()
