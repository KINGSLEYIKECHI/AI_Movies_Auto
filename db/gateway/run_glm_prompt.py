r"""
Send a prompt file to GLM (via the containerized Ollama), validate the
schema contract, and save the response — the containerized replacement
for Run_GLM_Test.ps1 / Run_GLM_Test_Streaming.ps1.

Usage (run via `docker compose exec gateway ...`):
    python run_glm_prompt.py GLM_Test_Prompt_Small.txt
    python run_glm_prompt.py GLM_Test_Prompt.txt
    python run_glm_prompt.py GLM_Episode_Prompt.txt

Saves to /app/outputs/GLM_Test_Output.json, which is mounted back to your
Windows host under db\gateway\outputs\.

NOTE: format:"json" is deliberately NOT sent — grammar-constrained
decoding is dramatically slower for large nested output, and the
Modelfile's system prompt already reliably produces clean JSON on its own.
"""

import json
import os
import re
import sys
from pathlib import Path

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "ollama:11434")
OLLAMA_URL = f"http://{OLLAMA_HOST}/api/generate"
MODEL_NAME = os.getenv("MODEL_NAME", "glm-film-director")

# What each stage's response MUST contain. Used two ways: (1) sanity-check
# whatever stage the model actually claims to have used, and (2) — the
# important one — catch it when the model's claimed stage doesn't match
# what the prompt actually asked for (e.g. asked for SERIES_OUTLINE, got
# back a PROJECT_BIBLE-shaped response instead).
STAGE_REQUIRED_KEYS = {
    "FULL": {"project", "characters", "locations", "episodes"},
    "PROJECT_BIBLE": {"project"},
    "CHARACTER_BIBLE": {"characters"},
    "LOCATION_BIBLE": {"locations"},
    "WARDROBE_BIBLE": {"wardrobe"},
    "PROP_BIBLE": {"props"},
    "SERIES_OUTLINE": {"project", "characters", "locations", "episode_outline"},
    "SCENE_PLAN": {"scene_plan"},
    "SHOT_PLAN": {"shots", "scene_ending_state"},
    "EPISODE_FULL": {"episodes"},
}


def extract_json(raw: str) -> str:
    """Strip markdown fences / stray text around the JSON, defensively —
    without format:"json" forcing grammar, GLM should still return clean
    JSON per its system prompt, but this is a safety net."""
    raw = raw.strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0]

    first_brace = raw.find("{")
    last_brace = raw.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        raw = raw[first_brace : last_brace + 1]
    return raw.strip()


def main():
    if len(sys.argv) != 2:
        print("Usage: python run_glm_prompt.py PROMPT_FILE")
        sys.exit(1)

    prompt_path = Path(sys.argv[1])
    if not prompt_path.exists():
        print(f"Prompt file not found: {prompt_path}")
        sys.exit(1)

    prompt = prompt_path.read_text(encoding="utf-8")

    target_match = re.search(r"Target runtime is approximately (\d+) seconds", prompt)
    target_seconds = int(target_match.group(1)) if target_match else None

    stage_match = re.search(r'Use stage "([A-Z_]+)"', prompt)
    requested_stage = stage_match.group(1) if stage_match else None

    print(f"Sending {prompt_path.name} to {MODEL_NAME} at {OLLAMA_URL} ...")

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": MODEL_NAME, "prompt": prompt, "stream": False},
            timeout=1800,  # long-running generations are expected
        )
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        print("REQUEST FAILED:")
        print(f"Ollama says: {resp.text}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print("REQUEST FAILED:")
        print(str(e))
        sys.exit(1)

    body = resp.json()
    raw_response = body.get("response", "")
    if not raw_response:
        print("Ollama returned no response body.")
        sys.exit(1)

    cleaned = extract_json(raw_response)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print("GLM output was not valid JSON:")
        print(raw_response)
        print(f"Parse error: {e}")
        sys.exit(1)

    actual_stage = result.get("stage")
    present_keys = set(result.keys())

    # THE ACTUAL FIX: verify GLM used the stage it was asked for. Without
    # this, a model reverting to an old/familiar response shape (e.g.
    # PROJECT_BIBLE instead of the requested SERIES_OUTLINE) gets saved and
    # "succeeds" here, only to fail confusingly several steps later when a
    # downstream script looks for data that was never actually produced.
    if requested_stage and actual_stage != requested_stage:
        out_dir = Path("outputs")
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "GLM_Test_Output.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"ERROR: requested stage \"{requested_stage}\" but GLM returned stage \"{actual_stage}\".")
        print(f"The response was saved to {out_path} for inspection, but NOT auto-loaded — "
              f"loading it would insert wrongly-shaped data.")
        print("This usually means the model reverted to an old/familiar response shape instead "
              "of following the requested stage. Try re-running this exact command; if it keeps "
              "happening for this stage, the Modelfile's instructions for it may need strengthening.")
        sys.exit(1)

    expected = STAGE_REQUIRED_KEYS.get(actual_stage)
    if expected:
        missing = expected - present_keys
        if missing:
            print(f"NOTE: stage \"{actual_stage}\" response is missing expected keys: {sorted(missing)}")
        else:
            print(f"Schema check passed: all keys required for stage \"{actual_stage}\" are present.")
    else:
        print(f"NOTE: unrecognized stage \"{actual_stage}\" — no key expectations defined for it.")

    out_dir = Path("outputs")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "GLM_Test_Output.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Stage returned: {result.get('stage')}")
    print(f"Characters returned: {len(result.get('characters', []))}")
    print(f"Locations returned: {len(result.get('locations', []))}")
    print(f"Episodes returned: {len(result.get('episodes', []))}")
    if "episode_outline" in result:
        print(f"Episode outline entries: {len(result['episode_outline'])}")
    if "scene_plan" in result:
        print(f"Scene plan entries: {len(result['scene_plan'])}")
    if "shots" in result:
        print(f"Shots returned: {len(result['shots'])}")

    for ep in result.get("episodes", []):
        total_seconds = sum(
            shot.get("duration_seconds", 0) or 0
            for scene in ep.get("scenes", [])
            for shot in scene.get("shots", [])
        )
        shot_count = sum(len(scene.get("shots", [])) for scene in ep.get("scenes", []))
        print(f"  {ep.get('episode_id')}: {shot_count} shots, ~{total_seconds}s total runtime")

        if target_seconds:
            diff_pct = abs(total_seconds - target_seconds) / target_seconds * 100
            if diff_pct > 25:
                print(f"  WARNING: requested ~{target_seconds}s but got ~{total_seconds}s "
                      f"({diff_pct:.0f}% off target) — GLM may not have followed the runtime instruction.")

    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()