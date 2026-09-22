r"""
Load a GLM production-JSON file into MySQL and create the matching
on-disk project folder structure.

Usage:
    python load_story_to_db.py path\to\GLM_Test_Output.json

Requires (pip install -r requirements.txt):
    mysql-connector-python
    python-dotenv

Reads connection settings from .env in this same folder (copy
.env.example to .env and fill in real values first).

This is idempotent: re-running against the same project_id UPDATEs
existing rows (via ON DUPLICATE KEY UPDATE) rather than duplicating
them, since GLM's staged production means the same project gets
revisited across multiple calls (PROJECT_BIBLE now, CHARACTER_BIBLE
later, etc.).
"""

import copy
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

BASE_PATH = Path(os.getenv("PROJECTS_BASE_PATH", r"D:\AI_Movies"))


def j(value):
    """Serialize a Python list/dict to a JSON string for a JSON column.
    Returns None if the value is missing, so JSON columns get SQL NULL
    instead of the literal string 'null'."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def execute_safe(cur, sql, params=None):
    """Wrapper around cursor.execute that defensively coerces any stray
    list/dict value into a JSON string before binding. Without format:"json"
    grammar enforcement, GLM occasionally returns an array where a plain
    text field was expected (e.g. continuity_rules as a list instead of a
    string) — MySQL's connector can't bind a raw Python list/dict as a
    parameter, so this prevents that from crashing the whole load."""
    if params is not None:
        params = tuple(
            json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            for v in params
        )
    cur.execute(sql, params)


ID_SCOPE_SEPARATOR = "__"


def scope_id(project_id: str, raw_id):
    """Prefix an entity ID with its project_id so character_id/location_id/
    etc. can never collide across two different projects (GLM naturally
    numbers CHAR_001, LOC_001, EP_001 fresh for every new story, so two
    projects WILL generate the same raw ID sooner or later). Idempotent —
    won't double-prefix an ID that's already scoped (e.g. one GLM echoed
    back verbatim from reference context)."""
    if not raw_id:
        return raw_id
    prefix = f"{project_id}{ID_SCOPE_SEPARATOR}"
    return raw_id if raw_id.startswith(prefix) else f"{prefix}{raw_id}"


def scope_ids(project_id: str, raw_ids):
    if not raw_ids:
        return raw_ids
    return [scope_id(project_id, r) for r in raw_ids]


def scope_all_ids(project_id: str, data: dict):
    """Mutates a (deep-copied) parsed JSON response in place, scoping every
    entity ID field to project_id before it ever reaches the database.
    Does NOT touch data['project']['project_id'] itself — that's already
    the project's own unique key. Only used for the DB-bound copy of the
    data; the original, unscoped data is what create_project_folders uses
    for filenames, since ':: '-free short IDs are fine on disk within a
    project's own already-namespaced folder."""
    for c in data.get("characters", []):
        c["character_id"] = scope_id(project_id, c.get("character_id"))
        for rel in c.get("relationships", []):
            rel["character_id"] = scope_id(project_id, rel.get("character_id"))
        if c.get("default_wardrobe_id"):
            c["default_wardrobe_id"] = scope_id(project_id, c["default_wardrobe_id"])
        c["prop_ids"] = scope_ids(project_id, c.get("prop_ids"))

    for loc in data.get("locations", []):
        loc["location_id"] = scope_id(project_id, loc.get("location_id"))
        loc["recurring_props"] = scope_ids(project_id, loc.get("recurring_props"))

    for w in data.get("wardrobe", []):
        w["wardrobe_id"] = scope_id(project_id, w.get("wardrobe_id"))
        w["character_id"] = scope_id(project_id, w.get("character_id"))

    for p in data.get("props", []):
        p["prop_id"] = scope_id(project_id, p.get("prop_id"))
        if p.get("owner_character_id"):
            p["owner_character_id"] = scope_id(project_id, p["owner_character_id"])
        if p.get("current_location_id"):
            p["current_location_id"] = scope_id(project_id, p["current_location_id"])

    for item in data.get("episode_outline", []):
        item["episode_id"] = scope_id(project_id, item.get("episode_id"))
        item["character_ids"] = scope_ids(project_id, item.get("character_ids"))
        item["location_ids"] = scope_ids(project_id, item.get("location_ids"))

    for item in data.get("scene_plan", []):
        item["scene_id"] = scope_id(project_id, item.get("scene_id"))
        item["episode_id"] = scope_id(project_id, item.get("episode_id"))
        if item.get("location_id"):
            item["location_id"] = scope_id(project_id, item["location_id"])
        item["character_ids"] = scope_ids(project_id, item.get("character_ids"))

    for shot in data.get("shots", []):
        shot["shot_id"] = scope_id(project_id, shot.get("shot_id"))
        shot["scene_id"] = scope_id(project_id, shot.get("scene_id"))
        shot["character_ids"] = scope_ids(project_id, shot.get("character_ids"))
        for line in shot.get("dialogue", []):
            if line.get("character_id"):
                line["character_id"] = scope_id(project_id, line["character_id"])

    for ep in data.get("episodes", []):
        ep["episode_id"] = scope_id(project_id, ep.get("episode_id"))
        ep["character_ids"] = scope_ids(project_id, ep.get("character_ids"))
        ep["location_ids"] = scope_ids(project_id, ep.get("location_ids"))

        for scene in ep.get("scenes", []):
            scene["scene_id"] = scope_id(project_id, scene.get("scene_id"))
            scene["episode_id"] = scope_id(project_id, scene.get("episode_id"))
            if scene.get("location_id"):
                scene["location_id"] = scope_id(project_id, scene["location_id"])
            scene["character_ids"] = scope_ids(project_id, scene.get("character_ids"))
            scene["wardrobe_ids"] = scope_ids(project_id, scene.get("wardrobe_ids"))
            scene["prop_ids"] = scope_ids(project_id, scene.get("prop_ids"))

            for shot in scene.get("shots", []):
                shot["shot_id"] = scope_id(project_id, shot.get("shot_id"))
                shot["scene_id"] = scope_id(project_id, shot.get("scene_id"))
                shot["character_ids"] = scope_ids(project_id, shot.get("character_ids"))
                for line in shot.get("dialogue", []):
                    if line.get("character_id"):
                        line["character_id"] = scope_id(project_id, line["character_id"])


def load_project(cursor, project: dict):
    execute_safe(cursor, 
        """
        INSERT INTO projects (
            project_id, title, genre, subgenre, tone, target_audience,
            logline, premise, themes, world_rules, setting, time_period,
            visual_style, color_language, narrative_pov, major_conflicts,
            stakes, ending, continuity_rules
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
            title=VALUES(title), genre=VALUES(genre), subgenre=VALUES(subgenre),
            tone=VALUES(tone), target_audience=VALUES(target_audience),
            logline=VALUES(logline), premise=VALUES(premise), themes=VALUES(themes),
            world_rules=VALUES(world_rules), setting=VALUES(setting),
            time_period=VALUES(time_period), visual_style=VALUES(visual_style),
            color_language=VALUES(color_language), narrative_pov=VALUES(narrative_pov),
            major_conflicts=VALUES(major_conflicts), stakes=VALUES(stakes),
            ending=VALUES(ending), continuity_rules=VALUES(continuity_rules)
        """,
        (
            project["project_id"], project.get("title"), project.get("genre"),
            project.get("subgenre"), project.get("tone"), project.get("target_audience"),
            project.get("logline"), project.get("premise"), j(project.get("themes")),
            j(project.get("world_rules")), project.get("setting"), project.get("time_period"),
            project.get("visual_style"), project.get("color_language"),
            project.get("narrative_pov"), j(project.get("major_conflicts")),
            project.get("stakes"), project.get("ending"), j(project.get("continuity_rules")),
        ),
    )


def load_characters(cursor, project_id: str, characters: list):
    for c in characters:
        execute_safe(cursor, 
            """
            INSERT INTO characters (
                character_id, project_id, name, age, role, personality, goals,
                fears, motivations, appearance, default_wardrobe_id, accessories,
                prop_ids, voice_characteristics, emotional_traits, character_arc,
                current_story_status
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name), age=VALUES(age), role=VALUES(role),
                personality=VALUES(personality), goals=VALUES(goals), fears=VALUES(fears),
                motivations=VALUES(motivations), appearance=VALUES(appearance),
                default_wardrobe_id=VALUES(default_wardrobe_id),
                accessories=VALUES(accessories), prop_ids=VALUES(prop_ids),
                voice_characteristics=VALUES(voice_characteristics),
                emotional_traits=VALUES(emotional_traits), character_arc=VALUES(character_arc),
                current_story_status=VALUES(current_story_status)
            """,
            (
                c["character_id"], project_id, c.get("name"), c.get("age"), c.get("role"),
                c.get("personality"), c.get("goals"), c.get("fears"), c.get("motivations"),
                j(c.get("appearance")), c.get("default_wardrobe_id") or None,
                j(c.get("accessories")), j(c.get("prop_ids")), c.get("voice_characteristics"),
                j(c.get("emotional_traits")), c.get("character_arc"), c.get("current_story_status"),
            ),
        )
        for rel in c.get("relationships", []):
            execute_safe(cursor, 
                """
                INSERT INTO character_relationships (character_id, related_character_id, relationship)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE relationship=VALUES(relationship)
                """,
                (c["character_id"], rel.get("character_id"), rel.get("relationship")),
            )


def load_locations(cursor, project_id: str, locations: list):
    for loc in locations:
        execute_safe(cursor, 
            """
            INSERT INTO locations (
                location_id, project_id, name, architecture, geography,
                interior_exterior, time_of_day_appearance, weather, lighting,
                color_palette, recurring_props, atmosphere, visual_landmarks,
                continuity_rules
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name), architecture=VALUES(architecture), geography=VALUES(geography),
                interior_exterior=VALUES(interior_exterior),
                time_of_day_appearance=VALUES(time_of_day_appearance), weather=VALUES(weather),
                lighting=VALUES(lighting), color_palette=VALUES(color_palette),
                recurring_props=VALUES(recurring_props), atmosphere=VALUES(atmosphere),
                visual_landmarks=VALUES(visual_landmarks), continuity_rules=VALUES(continuity_rules)
            """,
            (
                loc["location_id"], project_id, loc.get("name"), loc.get("architecture"),
                loc.get("geography"), loc.get("interior_exterior"),
                loc.get("time_of_day_appearance"), loc.get("weather"), loc.get("lighting"),
                loc.get("color_palette"), j(loc.get("recurring_props")), loc.get("atmosphere"),
                j(loc.get("visual_landmarks")), loc.get("continuity_rules"),
            ),
        )


def load_wardrobe(cursor, wardrobe: list):
    for w in wardrobe:
        execute_safe(cursor, 
            """
            INSERT INTO wardrobe (
                wardrobe_id, character_id, outfit_name, top, bottom, shoes,
                accessories, colors, materials, `condition`, weather_suitability,
                story_period
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                outfit_name=VALUES(outfit_name), top=VALUES(top), bottom=VALUES(bottom),
                shoes=VALUES(shoes), accessories=VALUES(accessories), colors=VALUES(colors),
                materials=VALUES(materials), `condition`=VALUES(`condition`),
                weather_suitability=VALUES(weather_suitability), story_period=VALUES(story_period)
            """,
            (
                w["wardrobe_id"], w.get("character_id"), w.get("outfit_name"), w.get("top"),
                w.get("bottom"), w.get("shoes"), j(w.get("accessories")), j(w.get("colors")),
                j(w.get("materials")), w.get("condition"), w.get("weather_suitability"),
                w.get("story_period"),
            ),
        )


def load_props(cursor, project_id: str, props: list):
    for p in props:
        execute_safe(cursor, 
            """
            INSERT INTO props (
                prop_id, project_id, name, appearance, material, color, size,
                owner_character_id, current_location_id, story_significance,
                current_status
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name), appearance=VALUES(appearance), material=VALUES(material),
                color=VALUES(color), size=VALUES(size),
                owner_character_id=VALUES(owner_character_id),
                current_location_id=VALUES(current_location_id),
                story_significance=VALUES(story_significance), current_status=VALUES(current_status)
            """,
            (
                p["prop_id"], project_id, p.get("name"), p.get("appearance"), p.get("material"),
                p.get("color"), p.get("size"), p.get("owner_character_id") or None,
                p.get("current_location_id") or None, p.get("story_significance"),
                p.get("current_status"),
            ),
        )


def load_episode_outline(cursor, project_id: str, outline: list):
    for item in outline:
        execute_safe(cursor,
            """
            INSERT INTO episode_outline (
                episode_id, project_id, episode_number, title, summary,
                character_ids, location_ids
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                episode_id=VALUES(episode_id), title=VALUES(title), summary=VALUES(summary),
                character_ids=VALUES(character_ids), location_ids=VALUES(location_ids)
            """,
            (
                item["episode_id"], project_id, item.get("episode_number"), item.get("title"),
                item.get("summary"), j(item.get("character_ids")), j(item.get("location_ids")),
            ),
        )


def load_scene_plan(cursor, project_id: str, scene_plan: list):
    for item in scene_plan:
        execute_safe(cursor,
            """
            INSERT INTO scene_plan (
                scene_id, project_id, episode_id, scene_number, beat,
                location_id, character_ids
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                scene_id=VALUES(scene_id), beat=VALUES(beat),
                location_id=VALUES(location_id), character_ids=VALUES(character_ids)
            """,
            (
                item["scene_id"], project_id, item.get("episode_id"), item.get("scene_number"),
                item.get("beat"), item.get("location_id") or None, j(item.get("character_ids")),
            ),
        )


def load_shots_flat(cursor, shots: list):
    """Loads a flat 'shots' array (SHOT_PLAN response) directly into the
    shots/shot_dialogue tables. The scene these belong to already exists
    (from scene_plan), so there's no episode/scene wrapper to unpack here —
    each shot already carries its own scene_id."""
    for shot in shots:
        execute_safe(cursor,
            """
            INSERT INTO shots (
                shot_id, scene_id, shot_type, framing, camera_position, lens,
                camera_movement, character_ids, character_action, facial_expression,
                environment, lighting, atmosphere, duration_seconds, sound_effects,
                music_direction, image_prompt, video_prompt, negative_prompt,
                continuity_notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                shot_type=VALUES(shot_type), framing=VALUES(framing),
                camera_position=VALUES(camera_position), lens=VALUES(lens),
                camera_movement=VALUES(camera_movement), character_ids=VALUES(character_ids),
                character_action=VALUES(character_action),
                facial_expression=VALUES(facial_expression), environment=VALUES(environment),
                lighting=VALUES(lighting), atmosphere=VALUES(atmosphere),
                duration_seconds=VALUES(duration_seconds), sound_effects=VALUES(sound_effects),
                music_direction=VALUES(music_direction), image_prompt=VALUES(image_prompt),
                video_prompt=VALUES(video_prompt), negative_prompt=VALUES(negative_prompt),
                continuity_notes=VALUES(continuity_notes)
            """,
            (
                shot["shot_id"], shot["scene_id"], shot.get("shot_type"),
                shot.get("framing"), shot.get("camera_position"), shot.get("lens"),
                shot.get("camera_movement"), j(shot.get("character_ids")),
                shot.get("character_action"), shot.get("facial_expression"),
                shot.get("environment"), shot.get("lighting"), shot.get("atmosphere"),
                shot.get("duration_seconds"), j(shot.get("sound_effects")),
                shot.get("music_direction"), shot.get("image_prompt"),
                shot.get("video_prompt"), shot.get("negative_prompt"),
                shot.get("continuity_notes"),
            ),
        )
        execute_safe(cursor, "DELETE FROM shot_dialogue WHERE shot_id = %s", (shot["shot_id"],))
        for order, line in enumerate(shot.get("dialogue", [])):
            execute_safe(cursor,
                """
                INSERT INTO shot_dialogue (shot_id, character_id, line, line_order)
                VALUES (%s, %s, %s, %s)
                """,
                (shot["shot_id"], line.get("character_id"), line.get("line"), order),
            )


def save_scene_continuity(cursor, project_id: str, episode_id: str, scene_id: str,
                           scene_number: int, ending_state: str):
    execute_safe(cursor,
        """
        INSERT INTO scene_continuity (project_id, episode_id, scene_id, scene_number, ending_state)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE scene_id=VALUES(scene_id), ending_state=VALUES(ending_state)
        """,
        (project_id, episode_id, scene_id, scene_number, ending_state),
    )


def load_episodes(cursor, project_id: str, episodes: list):
    for ep in episodes:
        execute_safe(cursor, 
            """
            INSERT INTO episodes (
                episode_id, project_id, title, objective, beginning, development,
                conflict, turning_point, climax, ending, cliffhanger,
                character_ids, location_ids, continuity_notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                title=VALUES(title), objective=VALUES(objective), beginning=VALUES(beginning),
                development=VALUES(development), conflict=VALUES(conflict),
                turning_point=VALUES(turning_point), climax=VALUES(climax), ending=VALUES(ending),
                cliffhanger=VALUES(cliffhanger), character_ids=VALUES(character_ids),
                location_ids=VALUES(location_ids), continuity_notes=VALUES(continuity_notes)
            """,
            (
                ep["episode_id"], project_id, ep.get("title"), ep.get("objective"),
                ep.get("beginning"), ep.get("development"), ep.get("conflict"),
                ep.get("turning_point"), ep.get("climax"), ep.get("ending"),
                ep.get("cliffhanger"), j(ep.get("character_ids")), j(ep.get("location_ids")),
                ep.get("continuity_notes"),
            ),
        )

        for scene in ep.get("scenes", []):
            execute_safe(cursor, 
                """
                INSERT INTO scenes (
                    scene_id, episode_id, purpose, location_id, time, weather,
                    character_ids, wardrobe_ids, prop_ids, action, emotional_state,
                    dialogue_summary, conflict, beginning_state, ending_state,
                    continuity_requirements
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    purpose=VALUES(purpose), location_id=VALUES(location_id), time=VALUES(time),
                    weather=VALUES(weather), character_ids=VALUES(character_ids),
                    wardrobe_ids=VALUES(wardrobe_ids), prop_ids=VALUES(prop_ids),
                    action=VALUES(action), emotional_state=VALUES(emotional_state),
                    dialogue_summary=VALUES(dialogue_summary), conflict=VALUES(conflict),
                    beginning_state=VALUES(beginning_state), ending_state=VALUES(ending_state),
                    continuity_requirements=VALUES(continuity_requirements)
                """,
                (
                    scene["scene_id"], ep["episode_id"], scene.get("purpose"),
                    scene.get("location_id") or None, scene.get("time"), scene.get("weather"),
                    j(scene.get("character_ids")), j(scene.get("wardrobe_ids")),
                    j(scene.get("prop_ids")), scene.get("action"), scene.get("emotional_state"),
                    scene.get("dialogue_summary"), scene.get("conflict"),
                    scene.get("beginning_state"), scene.get("ending_state"),
                    scene.get("continuity_requirements"),
                ),
            )

            for shot in scene.get("shots", []):
                execute_safe(cursor, 
                    """
                    INSERT INTO shots (
                        shot_id, scene_id, shot_type, framing, camera_position, lens,
                        camera_movement, character_ids, character_action, facial_expression,
                        environment, lighting, atmosphere, duration_seconds, sound_effects,
                        music_direction, image_prompt, video_prompt, negative_prompt,
                        continuity_notes
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        shot_type=VALUES(shot_type), framing=VALUES(framing),
                        camera_position=VALUES(camera_position), lens=VALUES(lens),
                        camera_movement=VALUES(camera_movement), character_ids=VALUES(character_ids),
                        character_action=VALUES(character_action),
                        facial_expression=VALUES(facial_expression), environment=VALUES(environment),
                        lighting=VALUES(lighting), atmosphere=VALUES(atmosphere),
                        duration_seconds=VALUES(duration_seconds), sound_effects=VALUES(sound_effects),
                        music_direction=VALUES(music_direction), image_prompt=VALUES(image_prompt),
                        video_prompt=VALUES(video_prompt), negative_prompt=VALUES(negative_prompt),
                        continuity_notes=VALUES(continuity_notes)
                    """,
                    (
                        shot["shot_id"], scene["scene_id"], shot.get("shot_type"),
                        shot.get("framing"), shot.get("camera_position"), shot.get("lens"),
                        shot.get("camera_movement"), j(shot.get("character_ids")),
                        shot.get("character_action"), shot.get("facial_expression"),
                        shot.get("environment"), shot.get("lighting"), shot.get("atmosphere"),
                        shot.get("duration_seconds"), j(shot.get("sound_effects")),
                        shot.get("music_direction"), shot.get("image_prompt"),
                        shot.get("video_prompt"), shot.get("negative_prompt"),
                        shot.get("continuity_notes"),
                    ),
                )

                # Dialogue lines are cleared and re-inserted per shot on reload,
                # since they're an ordered list, not a single-row update target.
                execute_safe(cursor, "DELETE FROM shot_dialogue WHERE shot_id = %s", (shot["shot_id"],))
                for order, line in enumerate(shot.get("dialogue", [])):
                    execute_safe(cursor, 
                        """
                        INSERT INTO shot_dialogue (shot_id, character_id, line, line_order)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (shot["shot_id"], line.get("character_id"), line.get("line"), order),
                    )


def create_project_folders(data: dict, project_id: str) -> Path:
    root = BASE_PATH / project_id

    (root / "story").mkdir(parents=True, exist_ok=True)
    (root / "assets" / "characters").mkdir(parents=True, exist_ok=True)
    (root / "assets" / "locations").mkdir(parents=True, exist_ok=True)
    (root / "assets" / "props").mkdir(parents=True, exist_ok=True)
    (root / "final").mkdir(parents=True, exist_ok=True)

    if data.get("project"):
        (root / "project.json").write_text(
            json.dumps(data["project"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (root / "story" / "story_bible.json").write_text(
            json.dumps(data["project"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if data.get("characters"):
        (root / "story" / "characters.json").write_text(
            json.dumps(data["characters"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if data.get("locations"):
        (root / "story" / "locations.json").write_text(
            json.dumps(data["locations"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if data.get("wardrobe"):
        (root / "story" / "wardrobe.json").write_text(
            json.dumps(data["wardrobe"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if data.get("props"):
        (root / "story" / "props.json").write_text(
            json.dumps(data["props"], ensure_ascii=False, indent=2), encoding="utf-8"
        )

    for ep in data.get("episodes", []):
        ep_root = root / "episodes" / ep["episode_id"]
        (ep_root / "scenes").mkdir(parents=True, exist_ok=True)
        (ep_root / "image_prompts").mkdir(parents=True, exist_ok=True)
        (ep_root / "video_prompts").mkdir(parents=True, exist_ok=True)
        (ep_root / "audio").mkdir(parents=True, exist_ok=True)

        (ep_root / "episode.json").write_text(
            json.dumps(ep, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        for scene in ep.get("scenes", []):
            (ep_root / "scenes" / f"{scene['scene_id']}.json").write_text(
                json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            for shot in scene.get("shots", []):
                (ep_root / "image_prompts" / f"{shot['shot_id']}.txt").write_text(
                    shot.get("image_prompt", ""), encoding="utf-8"
                )
                (ep_root / "video_prompts" / f"{shot['shot_id']}.txt").write_text(
                    shot.get("video_prompt", ""), encoding="utf-8"
                )

    return root


def save_continuity_snapshot(cursor, project_id: str, episodes: list):
    """After loading, snapshot 'where things stand' using fresh DB state
    (not the in-memory JSON), so this is correct even when GLM only sent
    partial character/prop updates for this episode."""
    if not episodes:
        return

    # episode_id convention is EP_NNN — take the highest-numbered one loaded this call
    latest = max(episodes, key=lambda e: e["episode_id"])
    # Split on the known scoping separator and take only the EP_NNN segment's
    # digits — reading digits from the whole ID picks up the project_id's own
    # digits too (e.g. PROJ_ECHO_001__EP_001 -> wrongly 1001 instead of 1).
    ep_segment = latest["episode_id"].split(ID_SCOPE_SEPARATOR)[-1]
    ep_digits = "".join(ch for ch in ep_segment if ch.isdigit())
    episode_number = int(ep_digits) if ep_digits else 0

    ending_state = None
    scenes = latest.get("scenes", [])
    if scenes:
        ending_state = scenes[-1].get("ending_state")

    execute_safe(cursor, 
        "SELECT character_id, current_story_status FROM characters WHERE project_id = %s",
        (project_id,),
    )
    character_status = {row[0]: row[1] for row in cursor.fetchall()}

    execute_safe(cursor, 
        "SELECT prop_id, current_status FROM props WHERE project_id = %s",
        (project_id,),
    )
    prop_status = {row[0]: row[1] for row in cursor.fetchall()}

    execute_safe(cursor, 
        """
        INSERT INTO continuity_snapshots
            (project_id, episode_id, episode_number, ending_state, character_status, prop_status)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            episode_id=VALUES(episode_id), ending_state=VALUES(ending_state),
            character_status=VALUES(character_status), prop_status=VALUES(prop_status)
        """,
        (
            project_id, latest["episode_id"], episode_number, ending_state,
            j(character_status), j(prop_status),
        ),
    )


def main():
    if len(sys.argv) not in (2, 3):
        print(r"Usage: python load_story_to_db.py path\to\output.json [PROJECT_ID]")
        print(r"  PROJECT_ID is required if the JSON has no top-level 'project' key")
        print(r"  (i.e. an EPISODE_FULL response for an already-established project).")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    with open(json_path, encoding="utf-8-sig") as f:  # utf-8-sig strips a BOM if present
        data = json.load(f)

    if data.get("project"):
        project_id = data["project"]["project_id"]
    elif len(sys.argv) == 3:
        project_id = sys.argv[2]
    else:
        print("ERROR: JSON has no 'project' key and no PROJECT_ID was given on the command line.")
        sys.exit(1)

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        # Scope entity IDs to project_id ONLY for the DB-bound copy — the
        # original `data` (unscoped, short IDs) is what create_project_folders
        # uses below, since filenames don't need the project prefix.
        scoped_data = copy.deepcopy(data)
        scope_all_ids(project_id, scoped_data)

        if scoped_data.get("project"):
            load_project(cursor, scoped_data["project"])
        load_characters(cursor, project_id, scoped_data.get("characters", []))
        load_locations(cursor, project_id, scoped_data.get("locations", []))
        load_wardrobe(cursor, scoped_data.get("wardrobe", []))
        load_props(cursor, project_id, scoped_data.get("props", []))
        load_episode_outline(cursor, project_id, scoped_data.get("episode_outline", []))
        load_scene_plan(cursor, project_id, scoped_data.get("scene_plan", []))
        load_episodes(cursor, project_id, scoped_data.get("episodes", []))
        if scoped_data.get("episodes"):
            save_continuity_snapshot(cursor, project_id, scoped_data["episodes"])

        if scoped_data.get("shots"):
            load_shots_flat(cursor, scoped_data["shots"])
            # A SHOT_PLAN response's shots all belong to one scene — look up
            # that scene's episode_id/scene_number from scene_plan (already
            # loaded, this call or earlier) to save its continuity pointer.
            scene_id = scoped_data["shots"][0]["scene_id"]
            cursor.execute(
                "SELECT episode_id, scene_number FROM scene_plan WHERE scene_id = %s",
                (scene_id,),
            )
            row = cursor.fetchone()
            if row and scoped_data.get("scene_ending_state"):
                episode_id, scene_number = row
                save_scene_continuity(cursor, project_id, episode_id, scene_id,
                                       scene_number, scoped_data["scene_ending_state"])
        conn.commit()
        print(f"Loaded project '{project_id}' into MySQL.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    root = create_project_folders(data, project_id)
    print(f"Created project folder tree at: {root}")


if __name__ == "__main__":
    main()
