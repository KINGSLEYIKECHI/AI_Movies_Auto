"""Dialogue, continuity, audio stems and measurable video checks.

Provider calls are opt-in. Generated mixed audio cannot be selectively unmixed.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import shutil
import uuid
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from asset_control import project_folder, scoped_file

VOICES=['alloy','ash','ballad','coral','echo','fable','onyx','nova','sage','shimmer','verse','marin','cedar']

class Direction(BaseModel):
    dialogue: bool=True
    effects: bool=True
    ambience: bool=False
    music: bool=False
    language: str=Field(default='English',min_length=1,max_length=80)
    series_style: str=Field(default='Consistent cinematic lighting, colour, wardrobe and camera language.',max_length=2000)
    audio_mode: Literal['native','separate']='native'
    speech_provider: Literal['none','openai']='none'
    verify_speech: bool=False
    continuity: Literal['planned','continuous']='planned'
    require_approved_previous: bool=False
    strict_timing: bool=True
    ending_hold: float=Field(default=1,ge=0.25,le=3)
    episode_music_id: str | None=None
    voices: dict[str,str]=Field(default_factory=dict)

class ShotDirection(BaseModel):
    transition: Literal['default','continuous','new_angle','new_scene']='default'
    start_state: str=Field(default='',max_length=2000)
    end_state: str=Field(default='',max_length=2000)
    effects_description: str=Field(default='Only sounds caused by the visible actions.',max_length=1000)
    voiceover: bool=False
    simultaneous_dialogue: bool=False
    dialogue_audio_id: str | None=None
    effects_audio_id: str | None=None
    ambience_audio_id: str | None=None

def config_path(project,base=None):return project_folder(project,base)/'video_direction.json'
def read_direction(project,base=None):
    path=config_path(project,base)
    return Direction(**json.loads(path.read_text(encoding='utf-8'))) if path.exists() else Direction()
def write_direction(project,value,base=None):
    path=config_path(project,base);path.parent.mkdir(parents=True,exist_ok=True)
    atomic_json(path,value.model_dump())
def read_shot_direction(project,shot_id,base=None):
    path=project_folder(project,base)/'shot_direction.json'
    data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return ShotDirection(**data.get(shot_id,{}))
def write_shot_direction(project,shot_id,value,base=None):
    path=project_folder(project,base)/'shot_direction.json'
    data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    data[shot_id]=value.model_dump();path.parent.mkdir(parents=True,exist_ok=True);atomic_json(path,data)
def atomic_json(path,data):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,default=str),encoding='utf-8');temporary.replace(path)
def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],text=True))
def duration(path):return float(probe(path)['format']['duration'])

def audio_file(project,identifier,base=None):
    if not re.fullmatch(r'[a-f0-9]{32}',identifier or ''):raise ValueError('Invalid audio upload ID')
    folder=project_folder(project,base)/'audio_inputs'
    record=json.loads(scoped_file(project,folder/(identifier+'.json'),base).read_text(encoding='utf-8'))
    path=scoped_file(project,folder/record['file'],base)
    if not path.is_file():raise ValueError('Audio file missing')
    return record,path
def save_audio(project,encoded,name,role,base=None):
    if role not in {'dialogue','effects','ambience','music'}:raise ValueError('Unknown audio role')
    if len(encoded)>28*1024*1024:raise ValueError('Audio upload exceeds 20 MB')
    data=base64.b64decode(encoded,validate=True)
    if not data or len(data)>20*1024*1024:raise ValueError('Audio upload exceeds 20 MB or is empty')
    suffix=Path(name).suffix.lower()
    if suffix not in {'.wav','.mp3','.m4a','.flac','.ogg'}:raise ValueError('Use WAV, MP3, M4A, FLAC or OGG')
    folder=project_folder(project,base)/'audio_inputs';folder.mkdir(parents=True,exist_ok=True)
    identifier=uuid.uuid4().hex;path=folder/(identifier+suffix);path.write_bytes(data)
    try:
        info=probe(path)
        if not any(s['codec_type']=='audio' for s in info['streams']):raise ValueError('Upload contains no audio stream')
        record={'id':identifier,'name':Path(name).name,'role':role,'file':path.name,'duration_seconds':float(info['format']['duration'])}
        atomic_json(folder/(identifier+'.json'),record);return record
    except Exception:path.unlink(missing_ok=True);raise

def dialogue_rows(cur,project,shot):
    cur.execute('SELECT d.character_id,d.line,d.line_order,c.name AS speaker_name FROM shot_dialogue d JOIN shots s ON s.shot_id=d.shot_id JOIN scene_plan sp ON sp.scene_id=s.scene_id LEFT JOIN characters c ON c.character_id=d.character_id AND c.project_id=sp.project_id WHERE sp.project_id=%s AND d.shot_id=%s ORDER BY d.line_order,d.id',(project,shot))
    return cur.fetchall()

def validate_resources(project,lines,config,shot,base=None,seconds=None):
    if (config.verify_speech or config.speech_provider=='openai') and not os.getenv('OPENAI_API_KEY'):raise ValueError('Configure OPENAI_API_KEY for optional speech services')
    if config.audio_mode!='separate':return
    if config.dialogue and lines:
        if not shot.dialogue_audio_id and config.speech_provider=='none':raise ValueError('Upload a dialogue track or enable speech generation for separate audio')
        if any(row.get('character_id') for row in lines) and not shot.voiceover and not os.getenv('LIPSYNC_COMMAND_JSON'):raise ValueError('Configure lip-sync for on-screen separate speech, or choose off-screen voiceover')
    if config.effects and not shot.effects_audio_id:raise ValueError('Upload an effects stem for separate audio, or disable effects. Mixed generated audio cannot preserve effects while removing music.')
    for role in ('dialogue','effects','ambience'):
        identifier=getattr(shot,role+'_audio_id')
        if identifier:
            record,path=audio_file(project,identifier,base)
            if record['role']!=role:raise ValueError('Audio upload has the wrong role')
            if role=='dialogue' and config.dialogue and seconds is not None and duration(path)>float(seconds)-config.ending_hold:
                raise ValueError('Uploaded dialogue exceeds the speaking window before the ending hold. Shorten the track (including trailing silence) or increase shot duration.')

def dialogue_windows(seconds,lines,config,shot):
    """Planning estimates for native speech; measured TTS timing is checked separately."""
    deadline=float(seconds)-config.ending_hold
    if deadline<=0:raise ValueError('Shot is too short for its ending hold')
    weights=[max(1,len(row['line'].split())) for row in lines]
    gap=0 if shot.simultaneous_dialogue else 0.15
    needed=(max(weights,default=0) if shot.simultaneous_dialogue else sum(weights))/2.5+gap*max(0,len(lines)-1)
    if needed>deadline:raise ValueError('Dialogue is too long for this shot, including speaker turns and ending hold. Shorten the lines or increase duration before rendering.')
    if not weights:return []
    available=deadline-gap*max(0,len(lines)-1)
    start=0;windows=[]
    for weight in weights:
        end=deadline if shot.simultaneous_dialogue else start+available*weight/sum(weights)
        windows.append((round(start,3),round(end,3)))
        start=0 if shot.simultaneous_dialogue else end+gap
    return windows

def compile_prompt(prompt,seconds,lines,config,shot,model,previous_prompt=''):
    usable=float(seconds)-config.ending_hold
    if usable<=0:raise ValueError('Shot is too short for its ending hold')
    spoken=[row for row in lines if row.get('line')] if config.dialogue else []
    windows=dialogue_windows(seconds,spoken,config,shot)
    if spoken and not shot.voiceover and any(not row.get('character_id') for row in spoken):
        raise ValueError('Assign each dialogue line to a character, or explicitly enable off-screen voiceover.')
    text=f'{config.series_style}\nStart state: {shot.start_state or "Match the starting frame and established scene state."}\n{prompt}\n'
    text+=f'Complete the single main action by {usable:g} seconds. Hold the completed end state through {seconds:g} seconds. End state: {shot.end_state or "A settled pose with the action completed; retain props and spatial positions."}\n'
    if previous_prompt:text+='Previous shot context (continuity only, do not repeat its action): '+previous_prompt+'\n'
    if config.audio_mode=='separate':text+='Visible speaking characters articulate the scripted lines naturally; audio will be replaced with controlled tracks.\n'
    if spoken:
        text+='The following dialogue assignments and timing override any speech instructions in the visual description. Deliver each exact line once, only by its assigned speaker; do not invent, repeat, share or stretch dialogue.\n'
        text+=('Simultaneous delivery is explicitly enabled: only the assigned speakers may overlap.\n' if shot.simultaneous_dialogue else 'Take turns in the listed order. No overlapping voices. Only the active speaker moves their lips; all listeners keep their mouths at rest.\n')
        if not shot.voiceover:text+='Keep every delivering character visibly on screen. Ensure accurate lip synchronization for all speaking characters: each line must match the mouth movements of its assigned speaker, with no off-screen voices, dubbing mismatch or another character mouthing the line.\n'
        text+=f'Finish every spoken word by {usable:g} seconds. From {usable:g} to {float(seconds):g} seconds, hold the completed pose with lips at rest and no speech; foreground sound effects may continue. Never cut a sentence to create the hold.\n'
    for index,row in enumerate(spoken):
        who=row.get('speaker_name') or row.get('character_id') or 'Narrator'
        identity=row.get('character_id') or 'Narrator'
        speaker='S'+str(int(hashlib.sha256(identity.encode()).hexdigest()[:6],16))
        offscreen=shot.voiceover or not row.get('character_id')
        delivery='says in an off-screen voiceover' if offscreen else 'speaks clearly with synchronised visible lip movement'
        start,end=windows[index]
        text+=f'Line {index+1}, speaking window {start:g}–{end:g} seconds: {who} ({speaker}) {delivery}: <d>[{config.language}] {row["line"]}</d>\n'
        if offscreen:text+='On-screen lips remain closed.\n'
    sound=shot.effects_description if config.effects else 'Quiet physical action.'
    if config.ambience:sound+=' Consistent low-level scene ambience.'
    else:sound+=' The soundscape contains only foreground action sounds and the specified voices.'
    if not config.effects and not config.ambience:sound='N/A'
    score='N/A'
    text=f'integrated_multimodal_description: [Shot 1] {text}\noverall_soundscape: {sound}\nnon_diegetic_music: {score}'
    if not spoken:text+='\nCharacters perform the action silently with their lips at rest.'
    return text

def predecessor(cur,project,job,approved=False):
    # Same scene only: continuity must not bridge an intentional scene change.
    cur.execute('SELECT s.shot_id,s.video_prompt FROM shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id WHERE sp.project_id=%s AND s.scene_id=%s AND s.shot_id<%s ORDER BY s.shot_id DESC LIMIT 1',(project,job.get('scene_id'),job['shot_id']))
    shot=cur.fetchone()
    if not shot:return None
    statuses="('approved')" if approved else "('approved','candidate')"
    cur.execute("SELECT id,output_path,status FROM asset_records WHERE project_id=%s AND entity_id=%s AND asset_type='shot_video' AND status IN "+statuses+" ORDER BY id DESC LIMIT 1",(project,shot['shot_id']))
    asset=cur.fetchone()
    return {**shot,'asset':asset}

def continuity_frame(project,job,previous,base=None):
    if not previous:return None
    asset=previous['asset']
    if not asset:raise ValueError('Generate and review the preceding shot before continuing this action, or choose a new angle.')
    source=scoped_file(project,asset['output_path'],base)
    if not source.is_file():raise ValueError('Preceding video is missing')
    output=scoped_file(project,Path(job['output_path']).with_suffix('.start.png'),base)
    output.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(['ffmpeg','-y','-sseof','-0.1','-i',str(source),'-frames:v','1',str(output)],check=True,capture_output=True)
    return output

def silence_ratio(path,seconds):
    result=subprocess.run(['ffmpeg','-i',str(path),'-af','silencedetect=noise=-40dB:d=0.25','-f','null','-'],capture_output=True,text=True,check=True)
    intervals=sum(float(n) for n in re.findall(r'silence_duration: ([\d.]+)',result.stderr))
    return min(1,intervals/max(seconds,0.01))

def quality_report(output,seconds,lines,config):
    info=probe(output);actual=float(info['format']['duration']);warnings=[]
    has_audio=any(s['codec_type']=='audio' for s in info['streams'])
    if abs(actual-float(seconds))>0.8:warnings.append(f'Clip is {actual:.2f}s; plan is {seconds}s. Regenerate or change timing before export.')
    if config.dialogue and lines:
        if not has_audio:warnings.append('Dialogue is planned but the clip has no audio.')
        elif silence_ratio(output,actual)>0.95:warnings.append('Dialogue is planned but the audio is almost silent.')
    report={'planned_seconds':seconds,'actual_seconds':actual,'has_audio':has_audio,'warnings':warnings,'action_completion':'Human review required','speech_check':'Not transcribed','dialogue_deadline_seconds':float(seconds)-config.ending_hold,'ending_hold_seconds':config.ending_hold,'speaker_and_lip_sync_check':'Human visual review required; transcription cannot verify speakers or lip synchronization'}
    if config.verify_speech and config.dialogue and lines and has_audio:
        from openai import OpenAI
        wav=Path(str(output)+'.verify.wav')
        try:
            subprocess.run(['ffmpeg','-y','-i',str(output),'-vn','-ar','16000','-ac','1',str(wav)],check=True,capture_output=True)
            with wav.open('rb') as file:transcript=OpenAI().audio.transcriptions.create(model=os.getenv('SPEECH_CHECK_MODEL','gpt-4o-mini-transcribe'),file=file).text
            expected=' '.join(row['line'] for row in lines).lower();heard=transcript.lower()
            words=set(re.findall(r'\w+',expected));coverage=len(words&set(re.findall(r'\w+',heard)))/max(len(words),1)
            report.update(transcript=transcript,speech_check='Transcribed',dialogue_word_coverage=coverage)
            if coverage<0.7:warnings.append('Transcript differs substantially from the planned dialogue. Review wording, language and speakers.')
        finally:wav.unlink(missing_ok=True)
    atomic_json(Path(str(output)+'.quality.json'),report);return report

def speech_track(project,output,lines,config,shot,seconds):
    """Stable configured character voices, cached per text/voice; explicit TTS opt-in."""
    from openai import OpenAI
    folder=Path(str(output)+'.audio');folder.mkdir(parents=True,exist_ok=True)
    pieces=[];offset=0;timing=[]
    for row in lines:
        voice=config.voices.get(row.get('character_id',''),'coral')
        if voice not in VOICES:raise ValueError('Select a supported voice for each character')
        key=hashlib.sha256(json.dumps([row['line'],voice,config.language,os.getenv('SPEECH_MODEL','gpt-4o-mini-tts')]).encode()).hexdigest()
        path=folder/(key+'.wav')
        if not path.exists():
            response=OpenAI().audio.speech.create(model=os.getenv('SPEECH_MODEL','gpt-4o-mini-tts'),voice=voice,input=row['line'],instructions=f'Speak clearly in {config.language}. Natural dramatic delivery.',response_format='wav')
            temporary=path.with_suffix('.tmp');response.stream_to_file(temporary);temporary.replace(path)
        length=duration(path)
        if offset+length>float(seconds)-config.ending_hold:raise ValueError('Generated speech exceeds the available shot time; shorten dialogue or increase duration.')
        pieces.append((path,offset));timing.append({'character_id':row.get('character_id'),'line':row['line'],'start_seconds':offset,'end_seconds':offset+length})
        offset=0 if shot.simultaneous_dialogue else offset+length+0.15
    if not pieces:return None
    target=folder/'dialogue.wav';args=['ffmpeg','-y'];filters=[]
    for i,(path,start) in enumerate(pieces):args+=['-i',str(path)];filters.append(f'[{i}:a]adelay={int(start*1000)}:all=1[a{i}]')
    filters.append(''.join(f'[a{i}]' for i in range(len(pieces)))+f'amix=inputs={len(pieces)}:normalize=0,apad[out]')
    subprocess.run(args+['-filter_complex',';'.join(filters),'-map','[out]','-t',str(seconds),str(target)],check=True,capture_output=True)
    atomic_json(Path(str(output)+'.dialogue_timing.json'),{'dialogue_deadline_seconds':float(seconds)-config.ending_hold,'simultaneous_dialogue':shot.simultaneous_dialogue,'lines':timing})
    return target

def apply_audio(project,output,lines,config,shot,seconds,base=None):
    if config.audio_mode!='separate':return
    tracks=[]
    if config.dialogue:
        if shot.dialogue_audio_id:
            _,track=audio_file(project,shot.dialogue_audio_id,base)
            if duration(track)>float(seconds)-config.ending_hold:
                raise ValueError('Uploaded dialogue exceeds the speaking window before the ending hold. Shorten the track (including trailing silence) or increase shot duration; speech will not be cut.')
            Path(str(output)+'.dialogue_timing.json').unlink(missing_ok=True)
            tracks.append(track)
        elif config.speech_provider=='openai' and lines:
            tracks.append(speech_track(project,output,lines,config,shot,seconds))
        elif lines:raise ValueError('Separate audio needs an uploaded dialogue track or enabled speech generation.')
    for enabled,identifier in [(config.effects,shot.effects_audio_id),(config.ambience,shot.ambience_audio_id)]:
        if enabled and identifier:tracks.append(audio_file(project,identifier,base)[1])
    if config.effects and not shot.effects_audio_id:print('Separate audio: no effects stem supplied; generated mixed audio is discarded.',flush=True)
    source=Path(output);raw=Path(str(output)+'.raw.mp4')
    if not raw.exists():shutil.copy2(source,raw)
    source=raw
    if config.dialogue and any(row.get('character_id') for row in lines) and not shot.voiceover:
        command=os.getenv('LIPSYNC_COMMAND_JSON')
        if not command:raise ValueError('On-screen separate dialogue requires a configured lip-sync backend, or select off-screen voiceover.')
        argv=json.loads(command);synced=Path(str(output)+'.synced.mp4')
        if not isinstance(argv,list) or not argv or any(not isinstance(arg,str) for arg in argv):raise ValueError('LIPSYNC_COMMAND_JSON must be an argument array')
        if not all(any(token in arg for arg in argv) for token in ('{video}','{audio}','{output}')):raise ValueError('Lip-sync arguments need {video}, {audio} and {output} placeholders')
        timing=Path(str(output)+'.dialogue_timing.json')
        if any('{timing}' in arg for arg in argv) and not timing.exists():raise ValueError('This lip-sync backend requires generated per-speaker timing; use generated speech instead of an uploaded mixed dialogue track.')
        subprocess.run([str(arg).replace('{video}',str(raw)).replace('{audio}',str(tracks[0])).replace('{output}',str(synced)).replace('{timing}',str(timing)) for arg in argv],check=True)
        probe(synced);source=synced
    args=['ffmpeg','-y','-i',str(source)]
    if tracks:
        for path in tracks:args+=['-i',str(path)]
        mix=''.join(f'[{i+1}:a]' for i in range(len(tracks)))+f'amix=inputs={len(tracks)}:normalize=0,alimiter=limit=0.95,apad[a]'
        args+=['-filter_complex',mix,'-map','0:v:0','-map','[a]','-c:a','aac','-t',str(seconds)]
    else:args+=['-an']
    temporary=Path(str(output)+'.mixed.mp4')
    subprocess.run(args+['-c:v','copy',str(temporary)],check=True,capture_output=True);temporary.replace(output)
    receipt=Path(str(output)+'.comfy.json')
    if receipt.exists():
        data=json.loads(receipt.read_text());data['download_sha256']=hashlib.sha256(Path(output).read_bytes()).hexdigest();atomic_json(receipt,data)

def add_episode_music(project,output,config,base=None):
    if not config.music or not config.episode_music_id:return
    _,music=audio_file(project,config.episode_music_id,base);temporary=output.with_name('scored.mp4')
    subprocess.run(['ffmpeg','-y','-i',str(output),'-stream_loop','-1','-i',str(music),'-filter_complex','[1:a]volume=0.18[m];[0:a][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]','-map','0:v:0','-map','[a]','-c:v','copy','-c:a','aac','-shortest',str(temporary)],check=True,capture_output=True);temporary.replace(output)
