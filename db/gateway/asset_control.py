"""Project-scoped reference uploads and immutable render-option sidecars."""
import base64
import io
import json
import os
import re
import uuid
import warnings
from pathlib import Path
from PIL import Image

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = {'character_reference', 'location_reference', 'prop_reference', 'shot_image'}
UNRESOLVED_REJECTIONS = """SELECT a.id FROM asset_records a
 WHERE a.project_id=%s AND a.status='rejected'
 AND NOT EXISTS (SELECT 1 FROM asset_records newer WHERE newer.project_id=a.project_id
   AND newer.asset_type=a.asset_type AND newer.entity_id <=> a.entity_id
   AND newer.id>a.id AND newer.status IN ('candidate','approved'))
 AND NOT EXISTS (SELECT 1 FROM jobs replacement WHERE replacement.project_id=a.project_id
   AND replacement.id=CAST(JSON_UNQUOTE(JSON_EXTRACT(a.metadata,'$.replacement_job_id')) AS UNSIGNED))
 AND NOT EXISTS (SELECT 1 FROM jobs j JOIN jobs original ON original.id=a.job_id
   WHERE j.project_id=a.project_id AND j.job_type=original.job_type AND j.id>original.id
   AND COALESCE(j.shot_id,j.character_id,j.location_id,j.prop_id)=COALESCE(original.shot_id,original.character_id,original.location_id,original.prop_id))"""

def project_folder(project_id, base=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', project_id):
        raise ValueError('Invalid project ID')
    base = Path(base or os.getenv('PROJECTS_BASE_PATH', '/ai_movies')).resolve()
    requested = base / project_id
    if requested.is_symlink() or getattr(requested, 'is_junction', lambda: False)():
        raise ValueError('Project folder is linked; resolve the link before editing or deleting it')
    target = requested.resolve()
    if target.parent != base or target == base or target != requested:
        raise ValueError('Project folder resolves outside the output directory')
    return target

def scoped_file(project_id, path, base=None):
    target = Path(path).resolve()
    if not target.is_relative_to(project_folder(project_id, base)):
        raise ValueError('File is outside this project')
    return target

def save_upload(project_id, encoded, name, role, base=None):
    if len(encoded) > (MAX_UPLOAD_BYTES * 4 // 3 + 8):
        raise ValueError('Reference image exceeds 10 MB')
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_UPLOAD_BYTES: raise ValueError('Reference image exceeds 10 MB')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {'PNG', 'JPEG', 'WEBP'}: raise ValueError('Use PNG, JPEG or WebP')
                if image.width * image.height > 25_000_000: raise ValueError('Reference image exceeds 25 megapixels')
                image.load()
                normalized = io.BytesIO()
                image.convert('RGB').save(normalized, format='PNG')
    except ValueError: raise
    except Exception as exc: raise ValueError('The reference is not a valid PNG, JPEG or WebP image') from exc
    folder = project_folder(project_id, base) / 'uploads'
    scoped_file(project_id, folder, base).mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    record = {'id': identifier, 'name': Path(name.replace('\\', '/')).name[:120], 'role': role}
    (folder / (identifier + '.png')).write_bytes(normalized.getvalue())
    (folder / (identifier + '.json')).write_text(json.dumps(record), encoding='utf-8')
    return record

def upload_reference(project_id, identifier, base=None):
    if not re.fullmatch(r'[a-f0-9]{32}', identifier): raise ValueError('Invalid reference ID')
    folder = project_folder(project_id, base) / 'uploads'
    image = scoped_file(project_id, folder / (identifier + '.png'), base)
    metadata = scoped_file(project_id, folder / (identifier + '.json'), base)
    if not image.is_file() or not metadata.is_file(): raise ValueError('Uploaded reference is missing from this project')
    return json.loads(metadata.read_text(encoding='utf-8')), image

def option_path(output): return Path(str(output) + '.options.json')

def read_options(project_id, output, base=None):
    path = scoped_file(project_id, option_path(output), base)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}

def write_options(project_id, output, options, base=None):
    path = scoped_file(project_id, option_path(output), base)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(options), encoding='utf-8')
    temporary.replace(path)

def additional_references(cur, project_id, options):
    references = []
    if options.get('use_source_image'):
        cur.execute('SELECT output_path FROM asset_records WHERE id=%s AND project_id=%s', (options['source_asset_id'], project_id))
        row = cur.fetchone()
        if not row: raise ValueError('Original asset is missing')
        path = scoped_file(project_id, row[0])
        if not path.is_file(): raise ValueError('Original image file is unavailable; disable editing the original and regenerate from the prompt')
        references.append((options['source_asset_id'], str(path), 'source image to modify'))
    for identifier in options.get('reference_upload_ids', []):
        record, path = upload_reference(project_id, identifier)
        references.append((None, str(path), record['role'] + ' reference: ' + record['name']))
    return references
