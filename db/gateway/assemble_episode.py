"""Assemble an episode from approved clips, preserving generated audio.
Dialogue captions use equal timing within each shot; they are not forced-aligned.
"""
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
import mysql.connector
from run_comfyui_reference_jobs import DB

def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],text=True))

def timestamp(seconds):
    milliseconds=round(seconds*1000)
    hours,milliseconds=divmod(milliseconds,3600000);minutes,milliseconds=divmod(milliseconds,60000);seconds,milliseconds=divmod(milliseconds,1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}'

def main():
    project,episode=sys.argv[1:3]
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value) for value in (project,episode)): raise RuntimeError('Invalid ID')
    conn=mysql.connector.connect(**DB);cur=conn.cursor(dictionary=True)
    try:
        cur.execute('SELECT s.shot_id FROM shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id WHERE sp.project_id=%s AND sp.episode_id=%s ORDER BY sp.scene_number,s.shot_id',(project,episode));shots=cur.fetchall()
        if not shots:raise RuntimeError('No planned shots for episode')
        folder=Path(os.getenv('PROJECTS_BASE_PATH','/ai_movies'))/project/'final'/episode/uuid.uuid4().hex;folder.mkdir(parents=True)
        clips=[];captions=[];offset=0.0
        for shot in shots:
            cur.execute("SELECT output_path FROM asset_records WHERE project_id=%s AND entity_id=%s AND asset_type='shot_video' AND status='approved' ORDER BY reviewed_at DESC,id DESC LIMIT 1",(project,shot['shot_id']));asset=cur.fetchone()
            if not asset:raise RuntimeError(f"Approve video for {shot['shot_id']} before assembly")
            source=Path(asset['output_path']);info=probe(source);duration=float(info['format']['duration']);target=folder/f'{len(clips):04}.mp4'
            args=['ffmpeg','-y','-i',str(source)]
            has_audio=any(s['codec_type']=='audio' for s in info['streams'])
            if not has_audio:args+=['-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000']
            args+=['-map','0:v:0','-map','0:a:0' if has_audio else '1:a:0','-vf','scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1','-r','24','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-t',str(duration),str(target)]
            subprocess.run(args,check=True,stdout=subprocess.DEVNULL);clips.append(target)
            cur.execute('SELECT line FROM shot_dialogue WHERE shot_id=%s ORDER BY line_order,id',(shot['shot_id'],));lines=[r['line'] for r in cur.fetchall() if r['line']]
            for index,line in enumerate(lines):captions.append(f'{len(captions)+1}\n{timestamp(offset+duration*index/len(lines))} --> {timestamp(offset+duration*(index+1)/len(lines))}\n{line}\n')
            offset+=duration
        manifest=folder/'clips.txt';manifest.write_text('\n'.join(f"file '{clip.name}'" for clip in clips),encoding='utf-8')
        output=folder/'episode.mp4'
        subprocess.run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(manifest),'-c','copy','-movflags','+faststart',str(output)],check=True,stdout=subprocess.DEVNULL)
        subtitles=folder/'episode.srt';subtitles.write_text('\n'.join(captions),encoding='utf-8')
        if not output.is_file() or not probe(output).get('streams'):raise RuntimeError('Export verification failed')
        cur.execute("INSERT INTO asset_records(project_id,asset_type,entity_id,output_path,generation_backend,metadata) VALUES(%s,'final_render',%s,%s,'ffmpeg',%s)",(project,episode,str(output),json.dumps({'subtitle_path':str(subtitles),'duration_seconds':offset,'caption_timing':'estimated within shots'})));conn.commit()
        print(f'Final export candidate: {output}\nSubtitles: {subtitles}')
    finally:cur.close();conn.close()
if __name__=='__main__':main()
