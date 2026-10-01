r"""
Build the SHOT_PLAN prompt for ONE scene (exactly 4 shots), giving the
model the full episode map for context, a continuity pointer (previous
scene's ending state, or the previous episode's actual ending if this is
scene 1), and only the characters/locations relevant to this specific scene.

Duration is read from the project's LOCKED model config (set once via
set_project_models.py) â€” never computed per call, never left to the
model's judgment. This is what guarantees every shot in a project uses
the same duration and the same video model, with no drift partway through.

Usage:
    python generate_shot_prompt.py PROJECT_ID EPISODE_NUMBER SCENE_NUMBER

Run set_project_models.py first if you haven't locked this project's
model/duration config yet.

Writes GLM_Shot_Prompt.txt, ready for:
    python run_glm_prompt.py GLM_Shot_Prompt.txt
    python load_story_to_db.py outputs/GLM_Test_Output.json PROJECT_ID
"""

import json
import sys
from pathlib import Path

import mysql.connector

from generate_episode_prompt import DB_CONFIG, fetch_bibles

SHOTS_PER_EPISODE = 16  # 4 scenes x 4 shots, fixed structure â€” sized for ~1-3 min episodes


def fetch_locked_config(cursor, project_id: str):
    cursor.execute(
        "SELECT image_model_key, video_model_key, locked_duration_seconds "
        "FROM project_model_config WHERE project_id = %s",
        (project_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise SystemExit(
            f"No model config locked for '{project_id}'. Run this first:\n"
            f"  python set_project_models.py {project_id} IMAGE_MODEL_KEY VIDEO_MODEL_KEY DURATION_SECONDS\n"
            f"  (python set_project_models.py --list to see available models)"
        )
    image_model_key, video_model_key, duration = row
    return {"image_model_key": image_model_key, "video_model_key": video_model_key, "duration_seconds": duration}


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


def fetch_previous_episode_ending(cursor, project_id: str, episode_number: int):
    """Find the previous episode's LAST scene's ending_state, chaining
    through scene_continuity directly â€” continuity_snapshots is only
    populated by the old full-episode-in-one-call path, never by this
    per-scene flow, so it can't be relied on here."""
    if episode_number <= 1:
        return None
    cursor.execute(
        "SELECT episode_id FROM scene_plan WHERE project_id = %s AND episode_id LIKE %s LIMIT 1",
        (project_id, f"%EP_{episode_number - 1:03d}"),
    )
    row = cursor.fetchone()
    if not row:
        return None
    prev_episode_id = row[0]
    cursor.execute(
        "SELECT ending_state FROM scene_continuity WHERE episode_id = %s "
        "ORDER BY scene_number DESC LIMIT 1",
        (prev_episode_id,),
    )
    row2 = cursor.fetchone()
    return row2[0] if row2 else None


def fetch_continuity_pointer(cursor, project_id: str, episode_id: str, episode_number: int, scene_number: int):
    """Scene-to-scene pointer within this episode, falling back to the
    PREVIOUS EPISODE's actual last scene ending (via scene_continuity,
    not the unused continuity_snapshots table) if this is scene 1."""
    if scene_number > 1:
        cursor.execute(
            "SELECT ending_state FROM scene_continuity "
            "WHERE episode_id = %s AND scene_number = %s",
            (episode_id, scene_number - 1),
        )
        row = cursor.fetchone()
        if row:
            return f"(from the previous scene in this episode) {row[0]}"
        # Previous scene in THIS episode was skipped/never generated â€” fall
        # through to whatever the last available continuity point is,
        # rather than silently claiming "no prior continuity" when there
        # actually is history, just with a gap in it.
        print(f"WARNING: scene {scene_number - 1} of this episode has no recorded "
              f"ending_state (likely skipped) â€” continuity pointer will fall back "
              f"to the previous episode's ending instead, which may be less precise.")

    prev_ending = fetch_previous_episode_ending(cursor, project_id, episode_number)
    if prev_ending:
        return f"(from the end of the previous episode) {prev_ending}"

    return "This is the very first scene of the series â€” no prior continuity."


def build_prompt(project_id: str, episode_number: int, scene_number: int) -> str:

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        config = fetch_locked_config(cursor, project_id)
        per_shot_seconds = config["duration_seconds"]

        # Need the episode_id string â€” derive it from any scene_plan row for this episode/number.
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

        continuity = fetch_continuity_pointer(cursor, project_id, episode_id, episode_number, scene_number)

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
    parts.append("FULL EPISODE MAP (all 4 scenes, for context â€” you are only generating shots for ONE of these):")
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
        f"Every shot's duration_seconds MUST be exactly {per_shot_seconds} â€” this is fixed by the "
        f"production pipeline, not your decision. Also return scene_ending_state summarizing how "
        f"this scene ends, for the next scene's continuity."
    )
    parts.append("")
    parts.append(
        "Dialogue must be specific to what's happening in each individual shot â€” do not repeat the "
        "same or near-identical line across shots or scenes."
    )
    parts.append("")
    parts.append("Return only the JSON object as defined by your output contract. No prose, no markdown fences.")

    return "\n".join(parts)


def main():
    if len(sys.argv) != 4:
        print("Usage: python generate_shot_prompt.py PROJECT_ID EPISODE_NUMBER SCENE_NUMBER")
        sys.exit(1)

    project_id = sys.argv[1]
    episode_number = int(sys.argv[2])
    scene_number = int(sys.argv[3])

    prompt = build_prompt(project_id, episode_number, scene_number)
    out_path = Path("GLM_Shot_Prompt.txt")
    out_path.write_text(prompt, encoding="utf-8")
    print(f"Wrote {out_path} ({len(prompt)} chars) for episode {episode_number}, scene {scene_number}.")


if __name__ == "__main__":
    main()
