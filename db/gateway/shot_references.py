"""Shared shot context for automatic rendering and the manual reference picker."""
import json
from asset_control import scoped_file

ROLES={'character_reference':'character_identity','location_reference':'location','prop_reference':'prop','shot_image':'previous_shot'}

def library(rows,project_id,base=None):
    assets=rows("""SELECT a.id,a.asset_type,a.entity_id,a.output_path,a.generation_model,
       COALESCE(c.name,l.name,p.name,a.entity_id) AS name
       FROM asset_records a
       LEFT JOIN characters c ON c.character_id=a.entity_id AND c.project_id=a.project_id
       LEFT JOIN locations l ON l.location_id=a.entity_id AND l.project_id=a.project_id
       LEFT JOIN props p ON p.prop_id=a.entity_id AND p.project_id=a.project_id
       WHERE a.project_id=%s AND a.status='approved'
       AND a.asset_type IN ('character_reference','location_reference','prop_reference','shot_image')
       ORDER BY a.asset_type,a.entity_id,a.reviewed_at DESC,a.id DESC""",(project_id,))
    for asset in assets:
        try:asset['available']=scoped_file(project_id,asset['output_path'],base).is_file()
        except ValueError:asset['available']=False
        asset['role']=ROLES[asset['asset_type']]
        asset['preview_url']=f"/projects/{project_id}/assets/{asset['id']}/file"
        asset['label']=f"{asset['name']} ({asset['entity_id']})"
    return assets

def context(rows,project_id,shot_id,base=None):
    shots=rows('SELECT s.shot_id,s.scene_id,s.character_ids,sp.location_id FROM shots s JOIN scene_plan sp ON sp.scene_id=s.scene_id WHERE sp.project_id=%s AND s.shot_id=%s',(project_id,shot_id))
    if not shots:raise ValueError('Shot is not in this project\'s scene plan')
    shot=shots[0]
    characters=shot['character_ids'] or []
    if isinstance(characters,str):characters=json.loads(characters)
    if not isinstance(characters,list):raise ValueError('Shot character IDs must be a list')
    required=[('character_reference',identifier) for identifier in dict.fromkeys(characters)]
    if shot.get('location_id'):required.append(('location_reference',shot['location_id']))
    assets=library(rows,project_id,base)
    references=[];missing=[]
    for kind,identifier in required:
        # Old exports can have raw IDs while the entity tables use scoped IDs.
        identifiers={identifier,project_id+'__'+identifier} if not identifier.startswith(project_id+'__') else {identifier}
        asset=next((item for item in assets if item['asset_type']==kind and item['entity_id'] in identifiers),None)
        if asset and asset['available']:references.append(asset)
        else:missing.append({'asset_type':kind,'entity_id':identifier,'reason':'Approved image file missing' if asset else 'Reference not yet accepted'})
    return {'shot_id':shot['shot_id'],'scene_id':shot['scene_id'],'character_ids':characters,'location_id':shot.get('location_id'),'automatic_references':references,'missing_references':missing,'assets':assets}

def selected_assets(rows,project_id,identifiers,base=None):
    assets=library(rows,project_id,base)
    selected=[]
    for identifier in dict.fromkeys(identifiers):
        asset=next((item for item in assets if item['id']==identifier),None)
        if not asset or not asset['available']:raise ValueError(f'Reference asset #{identifier} is not an accepted image available in this project')
        selected.append(asset)
    return selected

def render_references(assets):
    return [(asset['id'],asset['output_path'],asset['role']+': '+asset['label']) for asset in assets]

def deduplicate(references):
    result=[];seen=set()
    for reference in references:
        key=(reference[0],reference[1])
        if key in seen:continue
        seen.add(key);result.append(reference)
    return result
