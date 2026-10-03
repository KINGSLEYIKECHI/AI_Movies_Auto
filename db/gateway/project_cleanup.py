"""Delete a single project with a recoverable filesystem/SQL cleanup journal."""
import json
import os
import shutil
import uuid
from pathlib import Path
from asset_control import project_folder, scoped_file

def video_bundle(project_id, output, base):
    """Only this version's output and adjacent processing files, never shared uploads."""
    folder=project_folder(project_id,base);checked_tree(folder)
    output=scoped_file(project_id,output,base)
    if output.suffix.lower()!='.mp4':raise ValueError('Only MP4 video versions can be deleted here')
    return sorted([path for path in output.parent.iterdir() if path.name==output.name or path.name.startswith(output.name+'.') or path.name==output.stem+'.start.png']) if output.parent.exists() else []

def recover_asset_cleanup(conn,project_id,base):
    folder=project_folder(project_id,base);trash=folder/'.asset-deleting'
    checked_tree(trash)
    if not trash.exists():return []
    cur=conn.cursor(buffered=True);pending=[]
    try:
        for journal in trash.glob('*.json'):
            record=json.loads(journal.read_text(encoding='utf-8'))
            if not __import__('re').fullmatch('[a-f0-9]{32}',record['folder']):raise ValueError('Invalid asset cleanup journal')
            bundle=trash/record['folder'];checked_tree(bundle)
            cur.execute('SELECT id FROM asset_records WHERE project_id=%s AND id=%s',(project_id,record['asset_id']))
            exists=cur.fetchone()
            try:
                if exists:
                    for item in record['files']:
                        target=scoped_file(project_id,item['original'],base);saved=bundle/item['name']
                        if saved.parent!=bundle or target.is_relative_to(trash):raise ValueError('Invalid asset cleanup path')
                        if saved.exists():
                            if target.exists():raise ValueError('Cannot restore asset over an existing file')
                            saved.rename(target)
                if bundle.exists():shutil.rmtree(bundle)
                journal.unlink()
            except OSError:pending.append(record['asset_id'])
    finally:cur.close()
    return pending

def delete_video_asset(conn,project_id,asset_id,base):
    recover_asset_cleanup(conn,project_id,base)
    cur=conn.cursor(dictionary=True,buffered=True);moved=[];journal=None;bundle=None
    try:
        cur.execute('SELECT * FROM asset_records WHERE project_id=%s AND id=%s FOR UPDATE',(project_id,asset_id));asset=cur.fetchone()
        if not asset:raise ValueError('Video version not found in this project')
        if asset['asset_type'] not in {'shot_video','final_render'}:raise ValueError('This action deletes video versions only')
        if asset.get('job_id'):
            cur.execute('SELECT status FROM jobs WHERE id=%s AND project_id=%s',(asset['job_id'],project_id))
            owner=cur.fetchone()
            if owner and owner['status']!='done':raise ValueError('Resolve this video job before deleting its files')
        cur.execute('SELECT COUNT(*) AS n FROM job_asset_references WHERE asset_id=%s',(asset_id,))
        if cur.fetchone()['n']:raise ValueError('Another job uses this video as a reference. Keep it until those dependencies are removed.')
        cur.execute('SELECT COUNT(*) AS n FROM asset_records WHERE output_path=%s AND id<>%s',(asset['output_path'],asset_id))
        if cur.fetchone()['n']:raise ValueError('Another asset shares this file; deletion is blocked')
        cur.execute('SELECT COUNT(*) AS n FROM jobs WHERE output_path=%s AND (id<>%s OR %s IS NULL)',(asset['output_path'],asset.get('job_id'),asset.get('job_id')))
        if cur.fetchone()['n']:raise ValueError('Another job shares this output; deletion is blocked')
        paths=video_bundle(project_id,asset['output_path'],base)
        if asset['asset_type']=='final_render':
            output=scoped_file(project_id,asset['output_path'],base)
            # Modern exports own a unique UUID assembly folder; include its temporary clips/captions.
            if __import__('re').fullmatch('[a-f0-9]{32}',output.parent.name) and output.parent.parent.parent.name=='final':
                cur.execute('SELECT output_path FROM asset_records WHERE id<>%s',(asset_id,))
                if any(Path(row['output_path']).resolve().is_relative_to(output.parent) for row in cur.fetchall()):raise ValueError('Another asset uses this export folder')
                paths=sorted(output.parent.iterdir())
        trash=project_folder(project_id,base)/'.asset-deleting';checked_tree(trash);trash.mkdir(exist_ok=True)
        identifier=uuid.uuid4().hex;bundle=trash/identifier;bundle.mkdir();journal=trash/(identifier+'.json')
        journal.write_text(json.dumps({'folder':identifier,'asset_id':asset_id,'files':[{'original':str(path),'name':path.name} for path in paths]}),encoding='utf-8')
        for path in paths:
            target=bundle/path.name;path.rename(target);moved.append((path,target))
        cur.execute('DELETE FROM asset_records WHERE project_id=%s AND id=%s',(project_id,asset_id))
        if asset.get('job_id'):
            # Remove the now-unused historical job so a deleted last clip can be queued again.
            cur.execute('DELETE FROM jobs WHERE project_id=%s AND id=%s AND NOT EXISTS (SELECT 1 FROM asset_records WHERE job_id=%s)',(project_id,asset['job_id'],asset['job_id']))
        if asset['status']=='approved' and asset['asset_type']=='shot_video':
            cur.execute("UPDATE asset_records SET status='candidate' WHERE project_id=%s AND asset_type='final_render' AND status='approved'",(project_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        for original,saved in reversed(moved):saved.rename(original)
        if journal:journal.unlink(missing_ok=True)
        if bundle and bundle.exists():bundle.rmdir()
        raise
    finally:cur.close()
    try:
        shutil.rmtree(bundle);journal.unlink()
        return {'deleted':True,'cleanup_complete':True,'files_removed':len(paths)}
    except OSError:return {'deleted':True,'cleanup_complete':False,'message':'Video record deleted; file cleanup will retry on the next recovery check.'}

def checked_tree(folder):
    if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)(): raise ValueError('Project folder is a link; resolve it before deletion')
    if folder.exists():
        for parent, directories, files in os.walk(folder, followlinks=False):
            for name in directories + files:
                path = Path(parent) / name
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)(): raise ValueError('Project contains linked files or folders; remove the links before deletion')

def matching_runs(project_id, runs):
    records = []
    for path in runs.glob('*.json'):
        if path.is_symlink(): continue
        try:
            if json.loads(path.read_text(encoding='utf-8')).get('project_id') == project_id:
                uuid.UUID(path.stem)
                records.append(path)
        except (ValueError, OSError): continue
    return records

def finish_cleanup(journal, base, runs):
    record = json.loads(journal.read_text(encoding='utf-8'))
    if not __import__('re').fullmatch('[a-f0-9]{32}', record['folder']): raise ValueError('Invalid cleanup folder')
    trash = base / '.deleting' / record['folder']
    checked_tree(trash)
    trash = trash.resolve()
    if trash.parent != (base / '.deleting').resolve(): raise ValueError('Invalid cleanup folder')
    checked_tree(trash)
    if trash.exists(): shutil.rmtree(trash)
    for path in matching_runs(record['project_id'], runs):
        for suffix in ('.json', '.log', '.tmp'):
            item = path.with_suffix(suffix)
            if item.is_symlink(): item.unlink()
            elif item.is_file(): item.unlink()
    journal.unlink()

def delete_project(conn, project_id, base, runs):
    base, runs = Path(base).resolve(), Path(runs).resolve()
    folder = project_folder(project_id, base)
    checked_tree(folder)
    trash_root = base / '.deleting'
    if trash_root.is_symlink() or (trash_root.exists() and getattr(trash_root, 'is_junction', lambda: False)()): raise ValueError('Cleanup folder is linked')
    trash_root.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    trash, journal = trash_root / identifier, trash_root / (identifier + '.json')
    cur = conn.cursor()
    moved = False
    try:
        cur.execute("SELECT COUNT(*) FROM jobs WHERE project_id=%s AND status='running'", (project_id,))
        if cur.fetchone()[0]: raise ValueError('Project has running jobs. Resolve external submissions before deletion')
        cur.execute('SELECT COUNT(*) FROM job_asset_references r JOIN asset_records a ON a.id=r.asset_id JOIN jobs j ON j.id=r.job_id WHERE a.project_id=%s AND j.project_id<>%s', (project_id, project_id))
        if cur.fetchone()[0]: raise ValueError('Another project uses these assets; remove those references before deletion')
        cur.execute('SELECT COUNT(*) FROM jobs j LEFT JOIN characters c ON c.character_id=j.character_id LEFT JOIN locations l ON l.location_id=j.location_id LEFT JOIN props p ON p.prop_id=j.prop_id WHERE j.project_id<>%s AND (c.project_id=%s OR l.project_id=%s OR p.project_id=%s)', (project_id, project_id, project_id, project_id))
        if cur.fetchone()[0]: raise ValueError('Another project uses these characters, locations or props; resolve those links first')
        cur.execute('SELECT output_path FROM asset_records WHERE project_id=%s UNION SELECT output_path FROM jobs WHERE project_id=%s AND output_path IS NOT NULL', (project_id, project_id))
        for (path,) in cur.fetchall():
            scoped_file(project_id, path, base)
        # shots has no FK to either modern scene_plan or legacy scenes.
        cur.execute('DELETE r FROM job_asset_references r LEFT JOIN jobs j ON j.id=r.job_id LEFT JOIN asset_records a ON a.id=r.asset_id WHERE j.project_id=%s OR a.project_id=%s', (project_id, project_id))
        cur.execute('DELETE s FROM shots s LEFT JOIN scene_plan sp ON sp.scene_id=s.scene_id LEFT JOIN scenes sc ON sc.scene_id=s.scene_id LEFT JOIN episodes e ON e.episode_id=sc.episode_id WHERE sp.project_id=%s OR e.project_id=%s OR s.shot_id IN (SELECT j.shot_id FROM jobs j WHERE j.project_id=%s AND j.shot_id IS NOT NULL)', (project_id, project_id, project_id))
        cur.execute('DELETE FROM projects WHERE project_id=%s', (project_id,))
        if cur.rowcount != 1: raise ValueError('Project not found')
        journal.write_text(json.dumps({'project_id': project_id, 'folder': identifier, 'phase': 'prepared'}), encoding='utf-8')
        if folder.exists(): folder.rename(trash); moved = True
        conn.commit()
    except Exception:
        conn.rollback()
        if moved and trash.exists(): trash.rename(folder)
        journal.unlink(missing_ok=True)
        raise
    finally: cur.close()
    # A process crash after commit is recovered by checking project existence,
    # rather than relying on an atomic transaction across MySQL and disk.
    try:
        finish_cleanup(journal, base, runs)
        return {'deleted': True, 'cleanup_complete': True}
    except OSError:
        return {'deleted': True, 'cleanup_complete': False, 'message': 'Database deleted; file cleanup is pending. Retry cleanup.'}

def recover_cleanup(conn, base, runs):
    base = Path(base).resolve()
    folder = base / '.deleting'
    if not folder.exists(): return []
    if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)(): raise ValueError('Cleanup folder is linked')
    pending = []
    cur = conn.cursor()
    try:
        for journal in folder.glob('*.json'):
            record = json.loads(journal.read_text(encoding='utf-8'))
            cur.execute('SELECT project_id FROM projects WHERE project_id=%s', (record['project_id'],))
            if cur.fetchone():
                target = project_folder(record['project_id'], base)
                trash = folder / record['folder']
                if trash.parent != folder or not __import__('re').fullmatch('[a-f0-9]{32}', record['folder']): raise ValueError('Invalid cleanup journal')
                if trash.exists() and not target.exists(): trash.rename(target)
                journal.unlink()
            else:
                try: finish_cleanup(journal, base, Path(runs))
                except OSError: pending.append(record['project_id'])
    finally: cur.close()
    return pending
