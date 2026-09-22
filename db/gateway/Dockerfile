FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY load_story_to_db.py .
COPY generate_episode_prompt.py .
COPY generate_bootstrap_prompt.py .
COPY generate_scene_plan_prompt.py .
COPY generate_shot_prompt.py .
COPY run_glm_prompt.py .
COPY run_episode_batch.py .
COPY GLM_Test_Prompt.txt .
COPY GLM_Test_Prompt_Small.txt .

RUN mkdir -p /app/outputs
