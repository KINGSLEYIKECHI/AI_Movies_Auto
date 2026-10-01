import base64
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shot_references import context,selected_assets,render_references,deduplicate
from run_openai_reference_jobs import render,record_candidate

class ShotReferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);folder=self.base/'film';folder.mkdir()
        self.assets=[]
        for identifier,kind,name,entity in [(1,'character_reference','Ada','CHAR_01'),(2,'character_reference','Bayo','CHAR_02'),(3,'location_reference','Workshop','LOC_01'),(4,'character_reference','Other actor','CHAR_03')]:
            path=folder/f'{identifier}.png';path.write_bytes(b'fixture')
            self.assets.append(dict(id=identifier,asset_type=kind,name=name,entity_id=entity,output_path=str(path),generation_model='fixture'))
    def rows(self,sql,params):
        if sql.startswith('SELECT s.shot_id'):
            return [dict(shot_id='shot',scene_id='scene',character_ids='["CHAR_01","CHAR_02"]',location_id='LOC_01')]
        return [dict(item) for item in self.assets]
    def test_only_planned_cast_and_location_are_automatic(self):
        data=context(self.rows,'film','shot',self.base)
        self.assertEqual([a['id'] for a in data['automatic_references']],[1,2,3])
        self.assertEqual(data['missing_references'],[])
        self.assertIn('Ada (CHAR_01)',render_references(data['automatic_references'])[0][2])
    def test_missing_image_blocks_context(self):
        Path(self.assets[1]['output_path']).unlink()
        data=context(self.rows,'film','shot',self.base)
        self.assertEqual(data['missing_references'][0]['entity_id'],'CHAR_02')
    def test_unaccepted_or_foreign_asset_cannot_be_selected(self):
        with self.assertRaises(ValueError):selected_assets(self.rows,'film',[99],self.base)
    def test_foreign_shot_rejected(self):
        with self.assertRaises(ValueError):context(lambda sql,params:[],'film','foreign',self.base)
    def test_legacy_scoped_reference_matches(self):
        self.assets[0]['entity_id']='film__CHAR_01'
        self.assertEqual(context(self.rows,'film','shot',self.base)['automatic_references'][0]['id'],1)
    def test_extra_images_and_duplicates(self):
        refs=render_references(context(self.rows,'film','shot',self.base)['automatic_references'])
        extra=render_references(selected_assets(self.rows,'film',[1,4],self.base))
        self.assertEqual([r[0] for r in deduplicate(refs+extra)],[1,2,3,4])
    def test_model_receives_named_images_and_lineage_roles(self):
        refs=render_references(context(self.rows,'film','shot',self.base)['automatic_references'])
        client=Mock();client.images.edit.return_value=SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b'output').decode())])
        job=dict(id=20,job_type='shot_image',shot_id='shot',prompt='Ada and Bayo in the workshop',output_path=str(self.base/'film'/'output.png'))
        render(client,'fixture',job,refs)
        call=client.images.edit.call_args.kwargs
        self.assertEqual(len(call['image']),3)
        self.assertIn('Image 1: character identity: Ada (CHAR_01)',call['prompt'])
        self.assertIn('Image 3: location: Workshop (LOC_01)',call['prompt'])
        cur=Mock();record_candidate(cur,'film',job,'fixture',refs)
        roles=[c.args[1][2] for c in cur.execute.call_args_list if c.args[0].startswith('INSERT INTO job_asset_references')]
        self.assertEqual(roles,['character_identity','character_identity','location'])
