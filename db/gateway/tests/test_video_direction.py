import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import video_direction as direction
import automation_api as api
from fastapi.testclient import TestClient

class VideoDirectionTests(unittest.TestCase):
    def test_two_speakers_take_turns_and_finish_before_two_second_hold(self):
        lines=[{'character_id':'KEMI','line':'Who is there?'},{'character_id':'ADA','line':'It is me.'}]
        config=direction.Direction(ending_hold=2);shot=direction.ShotDirection()
        windows=direction.dialogue_windows(10,lines,config,shot)
        self.assertLess(windows[0][1],windows[1][0]);self.assertEqual(windows[-1][1],8)
        prompt=direction.compile_prompt('They talk.',10,lines,config,shot,'ltx')
        for rule in ['No overlapping voices','Finish every spoken word by 8 seconds','From 8 to 10 seconds','no off-screen voices','Only the active speaker']:
            self.assertIn(rule,prompt)
        self.assertEqual(prompt.count('<d>[English] Who is there?</d>'),1)

    def test_simultaneous_delivery_and_voiceover_are_explicit(self):
        lines=[{'character_id':'A','line':'Hello'},{'character_id':'B','line':'Hello'}]
        shot=direction.ShotDirection(simultaneous_dialogue=True)
        windows=direction.dialogue_windows(10,lines,direction.Direction(),shot)
        self.assertEqual(windows,[(0,9),(0,9)])
        self.assertIn('explicitly enabled',direction.compile_prompt('',10,lines,direction.Direction(),shot,'ltx'))
        with self.assertRaisesRegex(ValueError,'Assign each'):
            direction.compile_prompt('',10,[{'line':'Hello'}],direction.Direction(),direction.ShotDirection(),'ltx')
        self.assertIn('off-screen voiceover',direction.compile_prompt('',10,[{'line':'Hello'}],direction.Direction(),direction.ShotDirection(voiceover=True),'ltx'))

    def test_measured_speech_timing_reserves_hold_and_rejects_overflow(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,{'openai':Mock()}),patch('openai.OpenAI') as client,patch.object(direction,'duration',return_value=3),patch.object(direction.subprocess,'run') as run:
            client.return_value.audio.speech.create.return_value.stream_to_file.side_effect=lambda path:Path(path).write_bytes(b'speech')
            run.side_effect=lambda args,**kw:Path(args[-1]).write_bytes(b'mixed')
            output=Path(folder)/'clip.mp4';lines=[{'character_id':'A','line':'Hello'},{'character_id':'B','line':'Goodbye'}]
            direction.speech_track('film',output,lines,direction.Direction(ending_hold=2),direction.ShotDirection(),10)
            timing=json.loads(Path(str(output)+'.dialogue_timing.json').read_text())
            self.assertEqual(timing['dialogue_deadline_seconds'],8)
            self.assertLess(timing['lines'][0]['end_seconds'],timing['lines'][1]['start_seconds'])
            with patch.object(direction,'duration',return_value=4):
                with self.assertRaisesRegex(ValueError,'exceeds'):direction.speech_track('film',output,lines,direction.Direction(ending_hold=2),direction.ShotDirection(),10)

    def test_uploaded_speech_over_hold_deadline_fails_before_any_processing(self):
        with patch.object(direction,'audio_file',return_value=({'role':'dialogue'},Path('dialogue.wav'))),patch.object(direction,'duration',return_value=8.1),patch.object(direction.subprocess,'run') as run:
            config=direction.Direction(audio_mode='separate',effects=False,ending_hold=2)
            shot=direction.ShotDirection(voiceover=True,dialogue_audio_id='x')
            with self.assertRaisesRegex(ValueError,'ending hold'):
                direction.validate_resources('film',[{'line':'Hello'}],config,shot,seconds=10)
            with self.assertRaisesRegex(ValueError,'ending hold'):
                direction.apply_audio('film','clip.mp4',[{'line':'Hello'}],config,shot,10)
            run.assert_not_called()

    def test_transcript_mismatch_is_reported_without_claiming_speaker_validation(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(direction,'probe',return_value={'format':{'duration':10},'streams':[{'codec_type':'audio'}]}),patch.object(direction,'silence_ratio',return_value=0),patch.dict(sys.modules,{'openai':Mock()}),patch('openai.OpenAI') as client,patch.object(direction.subprocess,'run') as run:
            run.side_effect=lambda args,**kw:Path(args[-1]).write_bytes(b'audio')
            client.return_value.audio.transcriptions.create.return_value.text='Unrelated speech'
            report=direction.quality_report(Path(folder)/'clip.mp4',10,[{'line':'Who is there?'}],direction.Direction(verify_speech=True))
        self.assertEqual(report['speech_check'],'Transcribed');self.assertEqual(report['dialogue_word_coverage'],0);self.assertTrue(report['warnings'])
    def test_speech_uses_character_voice_and_caches_results(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,{'openai':Mock()}),patch('openai.OpenAI') as client,patch.object(direction,'duration',return_value=2),patch.object(direction.subprocess,'run') as run:
            client.return_value.audio.speech.create.return_value.stream_to_file.side_effect=lambda path:Path(path).write_bytes(b'speech')
            run.side_effect=lambda args,**kw:Path(args[-1]).write_bytes(b'mixed')
            config=direction.Direction(voices={'KEMI':'onyx'},speech_provider='openai');lines=[{'character_id':'KEMI','line':'Who is there?'}]
            for _ in range(2):direction.speech_track('film',Path(folder)/'clip.mp4',lines,config,direction.ShotDirection(),10)
            self.assertEqual(client.return_value.audio.speech.create.call_count,1)
            self.assertEqual(client.return_value.audio.speech.create.call_args.kwargs['voice'],'onyx')
    def test_strict_export_refuses_cutting_or_freezing_existing_clip(self):
        import assemble_episode as assembly
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'PROJECTS_BASE_PATH':folder}),patch.object(assembly.sys,'argv',['assemble','film','episode']),patch.object(assembly.mysql.connector,'connect') as connect,patch.object(assembly,'read_direction',return_value=direction.Direction()),patch.object(assembly,'probe',return_value={'format':{'duration':6},'streams':[{'codec_type':'audio'}]}),patch.object(assembly.subprocess,'run') as run:
            cursor=connect.return_value.cursor.return_value;cursor.fetchall.return_value=[{'shot_id':'shot','duration_seconds':10}];cursor.fetchone.return_value={'output_path':str(Path(folder)/'clip.mp4')}
            with self.assertRaisesRegex(RuntimeError,'cut action or freeze'):assembly.main()
        run.assert_not_called()
    def test_quality_review_requires_explicit_action_confirmation(self):
        with patch.object(api,'project'),patch.object(api,'require_idle'),patch.object(api.mysql.connector,'connect') as connect:
            connect.return_value.cursor.return_value.fetchone.return_value={'id':7,'status':'candidate','asset_type':'shot_video','metadata':{'quality':{'warnings':[]}}}
            result=TestClient(api.app).post('/projects/film/assets/7/review',json={'status':'approved'})
        self.assertEqual(result.status_code,400)
    def test_postprocessing_retry_reuses_receipt_instead_of_archiving_submission(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(api,'ROOT',Path(folder)),patch.object(api,'project'),patch.object(api,'require_idle'):
            output=Path(folder)/'film'/'clip.mp4';output.parent.mkdir();receipt=Path(str(output)+'.comfy.json');receipt.write_text(json.dumps({'phase':'processing_failed','prompt_id':'saved'}))
            with patch.object(api,'query',side_effect=[([{'job_type':'shot_video','output_path':str(output)}],1),([],1)]),patch.object(api,'saved_video') as saved:
                result=TestClient(api.app).post('/projects/film/jobs/81/retry')
            self.assertEqual(result.status_code,200);self.assertTrue(receipt.exists());saved.assert_not_called()
    def test_prompt_includes_exact_dialogue_no_score_and_completed_action(self):
        config=direction.Direction();shot=direction.ShotDirection(end_state='The door is open.')
        text=direction.compile_prompt('She opens the door.',10,[{'character_id':'KEMI','line':'Who is there?'}],config,shot,'minimax-h3')
        self.assertIn('<d>[English] Who is there?</d>',text);self.assertIn('by 9 seconds',text)
        self.assertIn('non_diegetic_music: N/A',text);self.assertIn('The door is open.',text)
        self.assertIn('foreground',text)
    def test_dialogue_disabled_and_too_long_dialogue_guard(self):
        lines=[{'line':'word '*40}]
        with self.assertRaisesRegex(ValueError,'too long'):direction.compile_prompt('Action',10,lines,direction.Direction(),direction.ShotDirection(),'ltx')
        text=direction.compile_prompt('Action',10,lines,direction.Direction(dialogue=False),direction.ShotDirection(),'ltx')
        self.assertNotIn('<d>',text)
    def test_direction_persistence_is_scoped_and_defaults_disable_background(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);config=direction.Direction(continuity='continuous');direction.write_direction('film',config,base)
            self.assertEqual(direction.read_direction('film',base).continuity,'continuous')
            self.assertFalse(direction.read_direction('other',base).music)
            direction.write_shot_direction('film','shot',direction.ShotDirection(end_state='Settled'),base)
            self.assertEqual(direction.read_shot_direction('film','shot',base).end_state,'Settled')
            with self.assertRaises(ValueError):direction.read_direction('../other',base)
    def test_audio_upload_checks_content_and_cross_project_access(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(direction,'probe',return_value={'streams':[{'codec_type':'audio'}],'format':{'duration':2}}):
            record=direction.save_audio('film',base64.b64encode(b'audio').decode(),'voice.wav','dialogue',Path(folder))
            self.assertTrue(direction.audio_file('film',record['id'],Path(folder))[1].is_file())
            with self.assertRaises(FileNotFoundError):direction.audio_file('other',record['id'],Path(folder))
            with self.assertRaises(ValueError):direction.save_audio('film','AAAA','voice.exe','dialogue',Path(folder))
    def test_invalid_audio_does_not_leave_file(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(direction,'probe',side_effect=ValueError('Invalid')):
            with self.assertRaises(ValueError):direction.save_audio('film','YQ==','bad.wav','effects',Path(folder))
            self.assertEqual(list(Path(folder,'film','audio_inputs').glob('*')),[])
    def test_final_frame_is_from_scoped_preceding_clip(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(direction.subprocess,'run') as run:
            base=Path(folder);clip=base/'film'/'old.mp4';clip.parent.mkdir();clip.write_bytes(b'clip')
            previous={'asset':{'id':7,'output_path':str(clip)}}
            result=direction.continuity_frame('film',{'output_path':str(clip.parent/'new.mp4')},previous,base)
            self.assertEqual(result.suffix,'.png');self.assertIn('-sseof',run.call_args.args[0])
            with self.assertRaisesRegex(ValueError,'preceding'):direction.continuity_frame('film',{'output_path':str(clip)}, {'asset':None},base)
    def test_separate_audio_does_not_silently_drop_required_effects(self):
        with self.assertRaisesRegex(ValueError,'effects stem'):direction.validate_resources('film',[],direction.Direction(audio_mode='separate'),direction.ShotDirection())
    def test_lipsync_is_required_for_on_screen_separate_speech(self):
        with patch.dict(os.environ,{'LIPSYNC_COMMAND_JSON':''}):
            with self.assertRaisesRegex(ValueError,'lip-sync'):direction.validate_resources('film',[{'character_id':'KEMI','line':'Hi'}],direction.Direction(audio_mode='separate',effects=False),direction.ShotDirection(dialogue_audio_id='x'))
    def test_quality_reports_duration_and_missing_dialogue_audio(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(direction,'probe',return_value={'format':{'duration':6},'streams':[{'codec_type':'video'}]}):
            output=Path(folder)/'clip.mp4'
            report=direction.quality_report(output,10,[{'line':'Hi'}],direction.Direction())
            self.assertEqual(len(report['warnings']),2);self.assertTrue(Path(str(output)+'.quality.json').is_file())
    def test_native_audio_is_not_destructively_processed(self):
        with patch.object(direction.subprocess,'run') as run:direction.apply_audio('film','unused',[],direction.Direction(),direction.ShotDirection(),10)
        run.assert_not_called()
    def test_separate_audio_retains_raw_and_excludes_original_mixed_track(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(direction.subprocess,'run') as run:
            output=Path(folder)/'film'/'clip.mp4';output.parent.mkdir();output.write_bytes(b'raw')
            def render(args,**kwargs):Path(args[-1]).write_bytes(b'mixed')
            run.side_effect=render
            direction.apply_audio('film',output,[],direction.Direction(audio_mode='separate',dialogue=False,effects=False),direction.ShotDirection(),10,Path(folder))
            self.assertEqual(output.read_bytes(),b'mixed');self.assertEqual(Path(str(output)+'.raw.mp4').read_bytes(),b'raw')
            self.assertIn('-an',run.call_args.args[0])
    def test_missing_key_prevents_opt_in_provider_calls(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}),patch.object(api,'project'),patch.object(api,'require_idle'):
            result=TestClient(api.app).put('/projects/film/video-direction',json={'speech_provider':'openai'})
        self.assertEqual(result.status_code,400)
    def test_shot_scope_rejects_other_projects(self):
        with patch.object(api,'project'),patch.object(api,'query',return_value=([],0)):
            result=TestClient(api.app).get('/projects/film/shots/other/direction')
        self.assertEqual(result.status_code,404)
