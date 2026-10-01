"""Asset revisions, reference-guided edits and scoped deletion checks."""
import base64
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from fastapi.testclient import TestClient
import automation_api as api
from asset_control import save_upload, upload_reference, write_options, additional_references, project_folder
from project_cleanup import delete_project, recover_cleanup
from run_openai_reference_jobs import render

def image_data():
    data = io.BytesIO(); Image.new('RGB', (16, 16), 'green').save(data, format='PNG')
    return data.getvalue()

class AssetControlTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.base = Path(self.folder.name)
        self.env = patch.dict(os.environ, {'PROJECTS_BASE_PATH': self.folder.name}); self.env.start()
        self.client = TestClient(api.app)
    def tearDown(self): self.env.stop(); self.folder.cleanup()

    def test_upload_normalizes_image_and_blocks_cross_project_id(self):
        reference = save_upload('film', base64.b64encode(image_data()).decode(), '../portrait.png', 'identity')
        record, path = upload_reference('film', reference['id'])
        self.assertEqual(record['name'], 'portrait.png'); self.assertTrue(path.is_file())
        with self.assertRaises(ValueError): upload_reference('other', reference['id'])
        with self.assertRaises(ValueError): upload_reference('film', '../portrait')
        with self.assertRaises(ValueError): save_upload('film', base64.b64encode(b'not image').decode(), 'bad.png', 'style')

    def test_original_and_uploaded_references_use_images_edit(self):
        reference = save_upload('film', base64.b64encode(image_data()).decode(), 'style.png', 'style')
        original = self.base / 'film' / 'original.png'; original.write_bytes(image_data())
        cursor = MagicMock(); cursor.fetchone.return_value = (str(original),)
        refs = additional_references(cursor, 'film', {'source_asset_id': 7, 'use_source_image': True, 'reference_upload_ids': [reference['id']]})
        client = MagicMock(); client.images.edit.return_value.data = [MagicMock(b64_json=base64.b64encode(image_data()).decode())]
        output = self.base / 'film' / 'new.png'
        render(client, 'configured-model', {'job_type': 'character_reference', 'prompt': 'Change the jacket only', 'output_path': str(output)}, refs)
        client.images.generate.assert_not_called()
        self.assertEqual(len(client.images.edit.call_args.kwargs['image']), 2)
        self.assertIn('Change the jacket only', client.images.edit.call_args.kwargs['prompt'])
        self.assertTrue(original.is_file()); self.assertTrue(output.is_file())

    def test_approved_version_can_be_rejected_and_dependents_need_review(self):
        conn = MagicMock(); cursor = conn.cursor.return_value
        cursor.fetchone.return_value = {'id': 5, 'status': 'approved', 'asset_type': 'character_reference', 'entity_id': 'CHAR_1'}
        cursor.rowcount = 2
        with patch.object(api, 'query', side_effect=[([{'project_id': 'film'}], 1), ([], 0)]), patch.object(api.mysql.connector, 'connect', return_value=conn), patch.object(api, 'read_settings', return_value=None):
            result = self.client.post('/projects/film/assets/5/review', json={'status': 'rejected'})
        self.assertEqual(result.status_code, 200); self.assertEqual(result.json()['affected_assets'], 2)
        conn.commit.assert_called_once()

    def test_legacy_rejected_asset_without_job_gets_replacement(self):
        original = self.base / 'film' / 'old.png'; original.parent.mkdir(); original.write_bytes(image_data())
        conn = MagicMock(); cursor = conn.cursor.return_value; cursor.fetchone.side_effect = [None, None]; cursor.lastrowid = 19
        asset = {'id': 5, 'status': 'rejected', 'asset_type': 'character_reference', 'entity_id': 'CHAR_1', 'job_id': None, 'output_path': str(original), 'original_prompt': 'Portrait'}
        with patch.object(api, 'ROOT', self.base), patch.object(api, 'query', side_effect=[([{'project_id': 'film'}], 1), ([], 0)]), patch.object(api, 'asset_edit_details', return_value=asset), patch.object(api.mysql.connector, 'connect', return_value=conn):
            result = self.client.post('/projects/film/assets/5/regenerate', json={'prompt': 'Portrait with blue jacket', 'use_source_image': True})
        self.assertEqual(result.status_code, 202); self.assertEqual(result.json()['id'], 19)
        inserted = next(call for call in cursor.execute.call_args_list if call.args[0].startswith('INSERT INTO jobs'))
        self.assertEqual(inserted.args[1][4], 'CHAR_1'); self.assertIn('blue jacket', inserted.args[1][8])
        self.assertTrue(original.is_file()); self.assertTrue(Path(result.json()['output_path'] + '.options.json').is_file())

    def test_revision_is_blocked_while_worker_runs(self):
        with patch.object(api, 'active', True), patch.object(api, 'query', return_value=([{'project_id': 'film'}], 1)), patch.object(api.mysql.connector, 'connect') as connect:
            result = self.client.post('/projects/film/assets/5/regenerate', json={})
        self.assertEqual(result.status_code, 409); connect.assert_not_called()

    def test_delete_requires_confirmation(self):
        with patch.object(api.mysql.connector, 'connect') as connect:
            result = self.client.request('DELETE', '/projects/film', json={'confirm_project_id': 'other'})
        self.assertEqual(result.status_code, 400); connect.assert_not_called()

    def test_linked_project_folder_is_not_treated_as_another_project(self):
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaises(ValueError): project_folder('film', self.base)

    def deletion_connection(self):
        conn = MagicMock(); cursor = conn.cursor.return_value
        cursor.fetchone.side_effect = [(0,), (0,), (0,)]; cursor.fetchall.return_value = []; cursor.rowcount = 1
        return conn, cursor

    def test_delete_cleans_project_files_logs_and_unlinked_shots_only(self):
        own = self.base / 'film'; own.mkdir(); (own / 'production.json').write_text('{}'); (own / 'media.png').write_bytes(image_data())
        other = self.base / 'other'; other.mkdir(); (other / 'keep.txt').write_text('keep')
        runs = self.base / 'runs'; runs.mkdir()
        own_run = runs / (str(uuid.uuid4()) + '.json'); own_run.write_text(json.dumps({'project_id': 'film'})); own_run.with_suffix('.log').write_text('log')
        other_run = runs / (str(uuid.uuid4()) + '.json'); other_run.write_text(json.dumps({'project_id': 'other'}))
        conn, cursor = self.deletion_connection()
        result = delete_project(conn, 'film', self.base, runs)
        self.assertTrue(result['cleanup_complete']); self.assertFalse(own.exists()); self.assertFalse(own_run.exists()); self.assertTrue(other_run.exists()); self.assertTrue((other / 'keep.txt').exists())
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any(sql.startswith('DELETE s FROM shots') for sql in statements))
        self.assertTrue(any(sql.startswith('DELETE r FROM job_asset_references') for sql in statements))
        conn.commit.assert_called_once()

    def test_failed_database_delete_restores_original_folder(self):
        own = self.base / 'film'; own.mkdir(); (own / 'keep.txt').write_text('keep')
        conn, _ = self.deletion_connection(); conn.commit.side_effect = RuntimeError('database failed')
        with self.assertRaises(RuntimeError): delete_project(conn, 'film', self.base, self.base / 'runs')
        self.assertTrue((own / 'keep.txt').is_file()); conn.rollback.assert_called_once()

    def test_file_cleanup_failure_can_be_retried_after_sql_delete(self):
        own = self.base / 'film'; own.mkdir(); (own / 'file.txt').write_text('data')
        conn, _ = self.deletion_connection()
        with patch('project_cleanup.shutil.rmtree', side_effect=PermissionError('locked')):
            result = delete_project(conn, 'film', self.base, self.base / 'runs')
        self.assertFalse(result['cleanup_complete']); self.assertTrue(list((self.base / '.deleting').glob('*.json')))
        recovery = MagicMock(); recovery.cursor.return_value.fetchone.return_value = None
        self.assertEqual(recover_cleanup(recovery, self.base, self.base / 'runs'), [])
        self.assertFalse(list((self.base / '.deleting').iterdir()))

    def test_delete_rejects_running_jobs_and_outside_paths(self):
        conn, cursor = self.deletion_connection(); cursor.fetchone.side_effect = [(1,)]
        with self.assertRaises(ValueError): delete_project(conn, 'film', self.base, self.base / 'runs')
        conn.commit.assert_not_called()
        conn, cursor = self.deletion_connection(); cursor.fetchall.return_value = [(str(self.base / 'other' / 'asset.png'),)]
        with self.assertRaises(ValueError): delete_project(conn, 'film', self.base, self.base / 'runs')
        conn.commit.assert_not_called()

if __name__ == '__main__': unittest.main()
