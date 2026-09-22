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
import sys
from pathlib import Path

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "ollama:11434")
OLLAMA_URL = f"http://{OLLAMA_HOST}/api/generate"
MODEL_NAME = os.getenv("MODEL_NAME", "glm-film-director")

EXPECTED_TOP_LEVEL_KEYS = {"stage", "project", "characters", "locations", "episodes"}


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

    import re
    target_match = re.search(r"Target runtime is approximately (\d+) seconds", prompt)
    target_seconds = int(target_match.group(1)) if target_match else None

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

    present_keys = set(result.keys())
    missing = EXPECTED_TOP_LEVEL_KEYS - present_keys
    # 'project'/'characters'/'locations' are legitimately absent on
    # episode-only (EPISODE_FULL) calls, so only warn, don't fail.
    if missing:
        print(f"NOTE: top-level keys not present in this response: {sorted(missing)} "
              f"(expected for EPISODE_FULL stage responses to an established project)")
    else:
        print("Schema check passed: stage/project/characters/locations/episodes all present.")

    out_dir = Path("outputs")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "GLM_Test_Output.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Stage returned: {result.get('stage')}")
    print(f"Characters returned: {len(result.get('characters', []))}")
    print(f"Locations returned: {len(result.get('locations', []))}")
    print(f"Episodes returned: {len(result.get('episodes', []))}")

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
