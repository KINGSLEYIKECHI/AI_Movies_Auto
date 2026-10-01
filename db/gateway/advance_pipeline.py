"""Advance an existing planned project one batch, stopping at human review."""
import subprocess
import sys
from pathlib import Path
import mysql.connector
from run_comfyui_reference_jobs import DB

HERE=Path(__file__).parent

def execute(script,*arguments):
    subprocess.run([sys.executable,'-u',str(HERE/script),*arguments],check=True)

def main():
    project=sys.argv[1]
    limit=sys.argv[sys.argv.index('--limit')+1] if '--limit' in sys.argv else '1'
    conn=mysql.connector.connect(**DB);cur=conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM asset_records WHERE project_id=%s AND status='candidate'",(project,))
        if cur.fetchone()[0]:print('WAITING_REVIEW: approve or reject candidate assets.');return
        cur.execute("SELECT COUNT(*) FROM jobs WHERE project_id=%s AND status IN ('failed','running')",(project,))
        if cur.fetchone()[0]:print('ATTENTION_REQUIRED: resolve failed/interrupted jobs before continuing.');return
        cur.execute("SELECT COUNT(*) FROM asset_records a WHERE a.project_id=%s AND a.status='rejected' AND NOT EXISTS (SELECT 1 FROM jobs j JOIN jobs original ON original.id=a.job_id WHERE j.project_id=a.project_id AND j.job_type=original.job_type AND j.id>original.id AND COALESCE(j.shot_id,j.character_id,j.location_id,j.prop_id)=COALESCE(original.shot_id,original.character_id,original.location_id,original.prop_id))",(project,))
        if cur.fetchone()[0]:print('WAITING_REGENERATION: queue a new version of rejected assets.');return
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
