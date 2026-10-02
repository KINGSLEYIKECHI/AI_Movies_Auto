"""Production settings, runtime budgets and editable prompt composition."""
import json
import os
import re
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from generate_bootstrap_prompt import build_prompt

class ProductionSpec(BaseModel):
    project_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,31}$')
    title: str = Field(min_length=1, max_length=200)
    concept: str = Field(min_length=10, max_length=12000)
    episodes: int = Field(default=1, ge=1, le=50)
    scenes_per_episode: int = Field(default=4, ge=1, le=30)
    shots_per_scene: int = Field(default=4, ge=1, le=30)
    episode_seconds: int = Field(default=120, ge=4, le=7200)
    characters: int = Field(default=3, ge=1, le=20)
    locations: int = Field(default=2, ge=1, le=20)
    visual_style: str = Field(default='Cinematic realism', max_length=2000)
    direction: str = Field(default='', max_length=6000)
    image_model_key: str = Field(min_length=1, max_length=64)
    video_model_key: Literal['ltx-2.5','minimax-h3'] = 'ltx-2.5'
    lora_mode: Literal['none','template','custom'] = 'none'
    lora_name: str = Field(default='', max_length=250)
    lora_strength: float = Field(default=1.0, ge=0, le=2)
    review_mode: Literal['every_stage','references_and_final'] = 'every_stage'
    bootstrap_prompt: str | None = Field(default=None, max_length=24000)
    @model_validator(mode='after')
    def validate_budget(self):
        shots=self.scenes_per_episode*self.shots_per_scene
        low,extra=divmod(self.episode_seconds,shots)
        if low<2 or low+(extra>0)>20:
            raise ValueError(f'{shots} shots need an episode duration between {shots*2} and {shots*20} seconds (2–20 seconds per shot).')
        if self.lora_mode=='template' and self.video_model_key!='minimax-h3':
            raise ValueError('The supplied template LoRA is available for MiniMax only. Choose a compatible installed LoRA for LTX.')
        if self.lora_mode=='custom' and not self.lora_name:
            raise ValueError('Choose an installed compatible LoRA.')
        if self.bootstrap_prompt and 'Use stage "SERIES_OUTLINE"' not in self.bootstrap_prompt:
            raise ValueError('Keep Use stage "SERIES_OUTLINE" in the outline prompt.')
        return self

def budget(spec):
    count=spec.scenes_per_episode*spec.shots_per_scene
    seconds,extra=divmod(spec.episode_seconds,count)
    return [seconds+(i<extra) for i in range(count)]

def outline_prompt(spec):
    return build_prompt(spec.concept,spec.characters,spec.locations,spec.episodes)+f'''
Production direction:
Title: {spec.title}
Project ID MUST be {spec.project_id}.
Visual style: {spec.visual_style}
Each episode has {spec.scenes_per_episode} scenes and {spec.shots_per_scene} shots per scene.
Target runtime per episode: {spec.episode_seconds} seconds.
Video engine: {spec.video_model_key}. Dialogue must fit the shot lengths.
Each shot has one achievable action, a clear starting state and a completed
ending state. Finish action and speech before the final second and leave a
settled ending hold. Preserve positions, props, wardrobe, lighting and screen
direction between shots. Put exact dialogue and speaker character IDs in the
dialogue array as well as the video prompt. Foreground effects only; no score
or ambient bed by default.
Establish the canonical wardrobe and important props in wardrobe and props arrays.
{spec.direction}
'''

def preview(spec):
    durations=budget(spec)
    return {'prompt':outline_prompt(spec),'shots_per_episode':len(durations),'total_shots':len(durations)*spec.episodes,
            'shot_seconds':durations,'episode_seconds':sum(durations),'estimated_model_calls':1+spec.episodes*(1+spec.scenes_per_episode),
            'summary':f'{spec.episodes} episode(s), {spec.scenes_per_episode} scenes each, {spec.shots_per_scene} shots per scene. {sum(durations)} seconds per episode. Shots: {min(durations)}–{max(durations)} seconds.'}

def root(project_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',project_id):raise ValueError('Invalid project ID')
    return Path(os.getenv('PROJECTS_BASE_PATH','/ai_movies'))/project_id

def read_settings(project_id):
    path=root(project_id)/'production.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None

def write_settings(project_id,data):
    path=root(project_id)/'production.json';path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)


def can_finish_stage_before_review(candidates, queued_types, review_mode='every_stage'):
    """Finish a stage's queued renders, then pause for one stage review."""
    groups = [set(['character_reference','location_reference','prop_reference']), {'shot_image'}, {'shot_video'}]
    remaining=set(candidates)
    if review_mode=='references_and_final':remaining-={'shot_image','shot_video'}
    if not remaining:return True
    matching=[group for group in groups if remaining<=group]
    return bool(matching and set(queued_types)&matching[0])
