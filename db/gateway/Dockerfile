FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY load_story_to_db.py .
COPY generate_episode_prompt.py .
COPY generate_bootstrap_prompt.py .
COPY generate_scene_plan_prompt.py .
COPY generate_shot_prompt.py .
COPY set_project_models.py .
COPY enqueue_asset_jobs.py .
COPY run_image_jobs.py .
COPY run_openai_reference_jobs.py .
COPY asset_review.py .
COPY automation_api.py run_comfyui_reference_jobs.py run_comfyui_video_jobs.py assemble_episode.py ./
COPY review.html studio.css studio.js advance_pipeline.py production_control.py plan_production.py ./
COPY asset_control.py project_cleanup.py ./
COPY run_glm_prompt.py .
COPY run_episode_batch.py .
COPY GLM_Test_Prompt.txt .
COPY GLM_Test_Prompt_Small.txt .

RUN mkdir -p /app/outputs
CMD ["python", "-m", "uvicorn", "automation_api:app", "--host", "0.0.0.0", "--port", "8000"]
