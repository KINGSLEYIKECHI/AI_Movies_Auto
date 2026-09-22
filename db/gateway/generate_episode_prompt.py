r"""
Build the prompt text for the NEXT episode of an already-established
project, pulling the story/character/location/wardrobe/prop bibles and
the latest continuity snapshot straight out of MySQL.

Usage:
    python generate_episode_prompt.py PROJECT_ID EPISODE_NUMBER MINUTES [AVG_CLIP_SECONDS]

Example:
    python generate_episode_prompt.py ECHO_ENGINE_01 2 2
        -> builds a prompt asking for episode 2, targeting a 2-minute
           runtime, written to GLM_Episode_Prompt.txt

Then feed that file straight into the existing test/production script:
    powershell -File Run_GLM_Test.ps1 -PromptFile GLM_Episode_Prompt.txt

The output of THAT then gets loaded back with:
    python load_story_to_db.py outputs\GLM_Test_Output.json PROJECT_ID
"""

import json
import os
import sys
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "glm_user"),
    "password": os.getenv("MYSQL_PASSWORD", "changeme_user"),
    "database": os.getenv("MYSQL_DATABASE", "glm_pipeline"),
}


def fetch_episode_outline(cursor, project_id: str, episode_number: int):
    cursor.execute(
        "SELECT episode_id, title, summary, character_ids, location_ids "
        "FROM episode_outline WHERE project_id = %s AND episode_number = %s",
        (project_id, episode_number),
    )
    row = cursor.fetchone()
    if not row:
        return None
    episode_id, title, summary, character_ids, location_ids = row
    return {
        "episode_id": episode_id,
        "title": title,
        "summary": summary,
        "character_ids": json.loads(character_ids) if character_ids else [],
        "location_ids": json.loads(location_ids) if location_ids else [],
    }


def fetch_bibles(cursor, project_id: str, character_ids=None, location_ids=None) -> dict:
    """Pull established bibles as compact reference JSON. When character_ids/
    location_ids are given (from this episode's outline entry), only THOSE
    specific characters/locations/props are included — not the entire cast —
    which is what keeps each episode's prompt small regardless of how large
    the overall series' cast grows. Falls back to the full cast/location
    list when no outline entry exists yet (e.g. episode 1 before an outline
    was ever generated, for backward compatibility)."""

    cursor.execute(
        "SELECT title, genre, tone, logline, premise, themes, world_rules, "
        "setting, visual_style, color_language, continuity_rules "
        "FROM projects WHERE project_id = %s",
        (project_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise SystemExit(f"No project found with id '{project_id}'. Load the bibles first.")

    cols = [d[0] for d in cursor.description]
    project = dict(zip(cols, row))
    for k in ("themes", "world_rules", "continuity_rules"):
        if project[k]:
            project[k] = json.loads(project[k]) if isinstance(project[k], str) else project[k]

    char_query = ("SELECT character_id, name, role, appearance, default_wardrobe_id, "
                  "current_story_status FROM characters WHERE project_id = %s")
    char_params = [project_id]
    if character_ids:
        char_query += " AND character_id IN (%s)" % ",".join(["%s"] * len(character_ids))
        char_params += character_ids
    cursor.execute(char_query, char_params)
    cols = [d[0] for d in cursor.description]
    characters = []
    for r in cursor.fetchall():
        c = dict(zip(cols, r))
        if c.get("appearance") and isinstance(c["appearance"], str):
            c["appearance"] = json.loads(c["appearance"])
        characters.append(c)

    loc_query = ("SELECT location_id, name, architecture, atmosphere, continuity_rules "
                 "FROM locations WHERE project_id = %s")
    loc_params = [project_id]
    if location_ids:
        loc_query += " AND location_id IN (%s)" % ",".join(["%s"] * len(location_ids))
        loc_params += location_ids
    cursor.execute(loc_query, loc_params)
    cols = [d[0] for d in cursor.description]
    locations = [dict(zip(cols, r)) for r in cursor.fetchall()]

    # Props relevant to this episode: owned by one of these characters OR
    # currently at one of these locations. Falls back to all props if no
    # character/location filter was given at all.
    prop_query = ("SELECT prop_id, name, appearance, owner_character_id, current_location_id, "
                  "current_status FROM props WHERE project_id = %s")
    prop_params = [project_id]
    if character_ids or location_ids:
        conditions = []
        if character_ids:
            conditions.append("owner_character_id IN (%s)" % ",".join(["%s"] * len(character_ids)))
            prop_params += character_ids
        if location_ids:
            conditions.append("current_location_id IN (%s)" % ",".join(["%s"] * len(location_ids)))
            prop_params += location_ids
        prop_query += " AND (" + " OR ".join(conditions) + ")"
    cursor.execute(prop_query, prop_params)
    cols = [d[0] for d in cursor.description]
    props = [dict(zip(cols, r)) for r in cursor.fetchall()]

    return {
        "project": project,
        "characters": characters,
        "locations": locations,
        "props": props,
    }


def fetch_latest_snapshot(cursor, project_id: str):
    cursor.execute(
        "SELECT episode_number, ending_state, character_status, prop_status "
        "FROM continuity_snapshots WHERE project_id = %s "
        "ORDER BY episode_number DESC, id DESC LIMIT 1",
        (project_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    episode_number, ending_state, character_status, prop_status = row
    return {
        "last_episode_number": episode_number,
        "ending_state": ending_state,
        "character_status": json.loads(character_status) if character_status else {},
        "prop_status": json.loads(prop_status) if prop_status else {},
    }


def build_prompt(project_id: str, episode_number: int, minutes: float, avg_clip_seconds: float) -> str:
    target_seconds = round(minutes * 60)
    target_shots = max(1, round(target_seconds / avg_clip_seconds))

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        outline = fetch_episode_outline(cursor, project_id, episode_number)
        char_ids = outline["character_ids"] if outline else None
        loc_ids = outline["location_ids"] if outline else None
        bibles = fetch_bibles(cursor, project_id, char_ids, loc_ids)
        snapshot = fetch_latest_snapshot(cursor, project_id)
    finally:
        cursor.close()
        conn.close()

    parts = []
    parts.append('Use stage "EPISODE_FULL".')
    parts.append("")
    parts.append(f"This is an established, ongoing project: {project_id}.")
    parts.append("The following is REFERENCE CONTEXT ONLY. Do not repeat it in your output.")
    parts.append("Reference existing characters/locations/props by their IDs below.")
    parts.append("")

    if outline:
        parts.append(f"SERIES OUTLINE FOR THIS EPISODE — {outline['title']}:")
        parts.append(outline["summary"] or "")
        parts.append(
            "Only the characters/locations relevant to THIS episode (per the series outline) "
            "are included below — that's intentional, not an oversight."
        )
        parts.append("")

    parts.append("PROJECT BIBLE (reference):")
    parts.append(json.dumps(bibles["project"], ensure_ascii=False))
    parts.append("")
    parts.append("RELEVANT CHARACTERS FOR THIS EPISODE (reference):"
                  if outline else "ESTABLISHED CHARACTERS (reference):")
    parts.append(json.dumps(bibles["characters"], ensure_ascii=False))
    parts.append("")
    parts.append("RELEVANT LOCATIONS FOR THIS EPISODE (reference):"
                  if outline else "ESTABLISHED LOCATIONS (reference):")
    parts.append(json.dumps(bibles["locations"], ensure_ascii=False))
    parts.append("")
    parts.append("RELEVANT PROPS FOR THIS EPISODE (reference):"
                  if outline else "ESTABLISHED PROPS (reference):")
    parts.append(json.dumps(bibles["props"], ensure_ascii=False))
    parts.append("")

    if snapshot:
        parts.append(f"CONTINUITY SNAPSHOT (as of episode {snapshot['last_episode_number']}):")
        parts.append(f"Ending state: {snapshot['ending_state']}")
        parts.append(f"Character status: {json.dumps(snapshot['character_status'], ensure_ascii=False)}")
        parts.append(f"Prop status: {json.dumps(snapshot['prop_status'], ensure_ascii=False)}")
        parts.append("")
    else:
        parts.append("CONTINUITY SNAPSHOT: none yet — this is the first episode after the bibles.")
        parts.append("")

    parts.append(
        f"TASK: Generate episode number {episode_number} (episode_id should follow the "
        f"EP_{episode_number:03d} convention). Target runtime is approximately "
        f"{target_seconds} seconds (~{minutes} minutes), which should take roughly "
        f"{target_shots} shots at an average of {avg_clip_seconds} seconds per clip. "
        "Pace scenes naturally rather than padding to hit the count exactly."
    )
    parts.append("")
    parts.append(
        "Only include \"characters\", \"locations\", or \"props\" in your response if "
        "something about them changed in this episode (full object for anything you include). "
        "Otherwise omit those keys entirely — do not resend anything unchanged."
    )
    parts.append("")
    parts.append("Return only the JSON object as defined by your output contract. No prose, no markdown fences.")

    return "\n".join(parts)


def main():
    if len(sys.argv) not in (4, 5):
        print("Usage: python generate_episode_prompt.py PROJECT_ID EPISODE_NUMBER MINUTES [AVG_CLIP_SECONDS]")
        sys.exit(1)

    project_id = sys.argv[1]
    episode_number = int(sys.argv[2])
    minutes = float(sys.argv[3])
    avg_clip_seconds = float(sys.argv[4]) if len(sys.argv) == 5 else 6.0

    prompt = build_prompt(project_id, episode_number, minutes, avg_clip_seconds)

    out_path = Path("GLM_Episode_Prompt.txt")
    out_path.write_text(prompt, encoding="utf-8")
    print(f"Wrote {out_path} ({len(prompt)} chars). Target: ~{round(minutes*60)}s, "
          f"~{max(1, round(minutes*60/avg_clip_seconds))} shots.")


if __name__ == "__main__":
    main()
