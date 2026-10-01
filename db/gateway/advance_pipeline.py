"""Advance an existing planned project one batch, stopping at human review."""
import subprocess
import sys
from pathlib import Path
import mysql.connector
from run_comfyui_reference_jobs import DB
from production_control import read_settings, can_finish_stage_before_review
from asset_control import UNRESOLVED_REJECTIONS

HERE=Path(__file__).parent

def execute(script,*arguments):
    subprocess.run([sys.executable,'-u',str(HERE/script),*arguments],check=True)

def main():
    project=sys.argv[1]
    limit=sys.argv[sys.argv.index('--limit')+1] if '--limit' in sys.argv else '1'
    conn=mysql.connector.connect(**DB);cur=conn.cursor()
    try:
        settings=read_settings(project)
        if settings and (settings.get('planning_status')!='ready' or not settings.get('prompts_approved')):
            print('WAITING_PROMPTS: finish planning and approve prompts in the interface.');return
        if settings and settings['spec'].get('review_mode')=='references_and_final':
            cur.execute("UPDATE asset_records SET status='approved',reviewed_at=NOW() WHERE project_id=%s AND asset_type IN ('shot_image','shot_video') AND status='candidate'",(project,));conn.commit()
        if settings:
            cur.execute("SELECT DISTINCT asset_type FROM asset_records WHERE project_id=%s AND status='candidate'",(project,));candidates=[r[0] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT job_type FROM jobs WHERE project_id=%s AND status='queued'",(project,));queued=[r[0] for r in cur.fetchall()]
            if not can_finish_stage_before_review(candidates,queued,settings['spec'].get('review_mode','every_stage')):
                print('WAITING_REVIEW: this stage is generated; approve or reject its assets.');return
        else:
            cur.execute("SELECT COUNT(*) FROM asset_records WHERE project_id=%s AND status='candidate'",(project,))
            if cur.fetchone()[0]:print('WAITING_REVIEW: approve or reject candidate assets.');return
        cur.execute("SELECT COUNT(*) FROM jobs WHERE project_id=%s AND status IN ('failed','running')",(project,))
        if cur.fetchone()[0]:print('ATTENTION_REQUIRED: resolve failed/interrupted jobs before continuing.');return
        cur.execute(UNRESOLVED_REJECTIONS,(project,))
        if cur.fetchone():print('WAITING_REGENERATION: queue a new version of rejected assets.');return
        execute('enqueue_asset_jobs.py',project,'references')
        execute('enqueue_asset_jobs.py',project,'shot-images')
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM jobs WHERE project_id=%s AND status='queued' AND job_type IN ('character_reference','location_reference','prop_reference','shot_image')",(project,))
        if cur.fetchone()[0]:execute('run_openai_reference_jobs.py',project,'--limit',limit);return
        execute('enqueue_asset_jobs.py',project,'shot-videos')
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM jobs WHERE project_id=%s AND status='queued' AND job_type='shot_video'",(project,))
        if cur.fetchone()[0]:execute('run_comfyui_video_jobs.py',project,'--limit',limit);return
        cur.execute('SELECT DISTINCT episode_id FROM scene_plan WHERE project_id=%s ORDER BY episode_id',(project,));episodes=[r[0] for r in cur.fetchall()]
        if not episodes:print('WAITING_PLANNING: generate scenes and shots first.');return
        for episode in episodes:
            cur.execute("SELECT COUNT(*) FROM asset_records WHERE project_id=%s AND entity_id=%s AND asset_type='final_render' AND status='approved'",(project,episode))
            if not cur.fetchone()[0]:execute('assemble_episode.py',project,episode);return
        print('COMPLETE: all planned episodes have approved exports.')
    finally:cur.close();conn.close()
if __name__=='__main__':main()
