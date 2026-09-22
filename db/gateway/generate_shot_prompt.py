r"""
Build the SHOT_PLAN prompt for ONE scene (exactly 5 shots), giving the
model the full 20-scene episode map for context, a continuity pointer
(previous scene's ending state, or the previous episode's ending state
if this is scene 1), and only the characters/locations relevant to this
specific scene.

Also computes the exact per-shot duration_seconds to use (from the
episode's target runtime, divided across the fixed 100 shots/episode,
then snapped to a valid LTX clip length) and tells the model to use that
exact value — duration is never left to the model's judgment.

Usage:
    python generate_shot_prompt.py PROJECT_ID EPISODE_NUMBER SCENE_NUMBER MINUTES_PER_EPISODE

Writes GLM_Shot_Prompt.txt, ready for:
    python run_glm_prompt.py GLM_Shot_Prompt.txt
    python load_story_to_db.py outputs/GLM_Test_Output.json PROJECT_ID
"""

import json
import sys
from pathlib import Path

import mysql.connector

from generate_episode_prompt import DB_CONFIG, fetch_bibles, fetch_latest_snapshot

SHOTS_PER_EPISODE = 16  # 4 scenes x 4 shots, fixed structure — sized for ~1-3 min episodes

# Valid discrete clip lengths your video model actually supports, in
# seconds, ascending. Confirm/adjust these against your real LTX/Wan
# model's supported values — this is a placeholder based on the two
# lengths mentioned when this was designed (8s / 10s). A predicted
# duration snaps UP to the nearest value in this list, never exceeding
# the last (largest) entry.
VALID_SHOT_DURATIONS = [8, 10]


def snap_duration(raw_seconds: float) -> int:
    for valid in VALID_SHOT_DURATIONS:
        if raw_seconds <= valid:
            return valid
    return VALID_SHOT_DURATIONS[-1]  # never exceed the max


def fetch_full_scene_plan(cursor, episode_id: str):
    cursor.execute(
        "SELECT scene_number, scene_id, beat, location_id, character_ids "
        "FROM scene_plan WHERE episode_id = %s ORDER BY scene_number",
        (episode_id,),
    )
    cols = [d[0] for d in cursor.description]
    rows = []
    for r in cursor.fetchall():
        item = dict(zip(cols, r))
        if item.get("character_ids"):
            item["character_ids"] = json.loads(item["character_ids"])
        rows.append(item)
    return rows


def fetch_this_scene(cursor, episode_id: str, scene_number: int):
    cursor.execute(
        "SELECT scene_id, beat, location_id, character_ids "
        "FROM scene_plan WHERE episode_id = %s AND scene_number = %s",
        (episode_id, scene_number),
    )
    row = cursor.fetchone()
    if not row:
        return None
    scene_id, beat, location_id, character_ids = row
    return {
        "scene_id": scene_id,
        "beat": beat,
        "location_id": location_id,
        "character_ids": json.loads(character_ids) if character_ids else [],
    }


def fetch_continuity_pointer(cursor, project_id: str, episode_id: str, scene_number: int):
    """Scene-to-scene pointer within this episode, falling back to the
    episode-level snapshot if this is scene 1 (continuing from the
    previous episode's ending instead)."""
    if scene_number > 1:
        cursor.execute(
            "SELECT ending_state FROM scene_continuity "
            "WHERE episode_id = %s AND scene_number = %s",
            (episode_id, scene_number - 1),
        )
        row = cursor.fetchone()
        if row:
            return f"(from the previous scene in this episode) {row[0]}"

    snapshot = fetch_latest_snapshot(cursor, project_id)
    if snapshot:
        return f"(from the end of the previous episode) {snapshot['ending_state']}"

    return "This is the very first scene of the series — no prior continuity."


def build_prompt(project_id: str, episode_number: int, scene_number: int, minutes_per_episode: float) -> str:
    per_shot_seconds = snap_duration((minutes_per_episode * 60) / SHOTS_PER_EPISODE)

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        # Need the episode_id string — derive it from any scene_plan row for this episode/number.
        cursor.execute(
            "SELECT episode_id FROM scene_plan WHERE project_id = %s AND scene_number = 1 "
            "AND episode_id LIKE %s LIMIT 1",
            (project_id, f"%EP_{episode_number:03d}"),
        )
        row = cursor.fetchone()
        if not row:
            raise SystemExit(
                f"No scene_plan found for episode {episode_number} of '{project_id}'. "
                f"Run generate_scene_plan_prompt.py for this episode first."
            )
        episode_id = row[0]

        full_map = fetch_full_scene_plan(cursor, episode_id)
        this_scene = fetch_this_scene(cursor, episode_id, scene_number)
        if not this_scene:
            raise SystemExit(f"Scene {scene_number} not found in episode {episode_number}'s plan.")

        continuity = fetch_continuity_pointer(cursor, project_id, episode_id, scene_number)

        loc_ids = [this_scene["location_id"]] if this_scene["location_id"] else []
        bibles = fetch_bibles(cursor, project_id, this_scene["character_ids"], loc_ids)
    finally:
        cursor.close()
        conn.close()

    parts = []
    parts.append('Use stage "SHOT_PLAN".')
    parts.append("")
    parts.append(f"Project: {project_id}, Episode {episode_number}, Scene {scene_number} "
                 f"(scene_id: {this_scene['scene_id']}).")
    parts.append("")
    parts.append("FULL EPISODE MAP (all 20 scenes, for context — you are only generating shots for ONE of these):")
    parts.append(json.dumps(full_map, ensure_ascii=False))
    parts.append("")
    parts.append(f"THIS SCENE'S BEAT: {this_scene['beat']}")
    parts.append("")
    parts.append(f"CONTINUITY POINTER: {continuity}")
    parts.append("")
    parts.append("RELEVANT CHARACTERS for this scene (reference):")
    parts.append(json.dumps(bibles["characters"], ensure_ascii=False))
    parts.append("")
    parts.append("RELEVANT LOCATIONS for this scene (reference):")
    parts.append(json.dumps(bibles["locations"], ensure_ascii=False))
    parts.append("")
    parts.append("RELEVANT PROPS for this scene (reference):")
    parts.append(json.dumps(bibles["props"], ensure_ascii=False))
    parts.append("")
    parts.append(
        f"TASK: Generate exactly 4 shots for scene_id \"{this_scene['scene_id']}\" "
        f"(shot_id format: {this_scene['scene_id']}_SH_01 through {this_scene['scene_id']}_SH_04). "
        f"Every shot's duration_seconds MUST be exactly {per_shot_seconds} — this is fixed by the "
        f"production pipeline, not your decision. Also return scene_ending_state summarizing how "
        f"this scene ends, for the next scene's continuity."
    )
    parts.append("")
    parts.append(
        "Dialogue must be specific to what's happening in each individual shot — do not repeat the "
        "same or near-identical line across shots or scenes."
    )
    parts.append("")
    parts.append("Return only the JSON object as defined by your output contract. No prose, no markdown fences.")

    return "\n".join(parts)


def main():
    if len(sys.argv) != 5:
        print("Usage: python generate_shot_prompt.py PROJECT_ID EPISODE_NUMBER SCENE_NUMBER MINUTES_PER_EPISODE")
        sys.exit(1)

    project_id = sys.argv[1]
    episode_number = int(sys.argv[2])
    scene_number = int(sys.argv[3])
    minutes_per_episode = float(sys.argv[4])

    prompt = build_prompt(project_id, episode_number, scene_number, minutes_per_episode)
    out_path = Path("GLM_Shot_Prompt.txt")
    out_path.write_text(prompt, encoding="utf-8")
    print(f"Wrote {out_path} ({len(prompt)} chars) for episode {episode_number}, scene {scene_number}.")


if __name__ == "__main__":
    main()