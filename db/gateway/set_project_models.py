r"""
Lock a project's image model, video model, and shot duration — ONCE.
Every shot generated after this reads the SAME locked values, so a project
never drifts between models or durations partway through (e.g. starting
at 8s on Wan2.2 and later switching to LTX at 10s mid-series).

Usage:
    python set_project_models.py PROJECT_ID IMAGE_MODEL_KEY VIDEO_MODEL_KEY DURATION_SECONDS [--force]

List available models first:
    python set_project_models.py --list

Example:
    python set_project_models.py nigerian_mechanic_past_machine flux1-dev wan2.2 8

--force is required to CHANGE an already-locked project's config — this is
deliberate friction, not a bug, since changing models/duration partway
through a project is exactly the inconsistency this exists to prevent.
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


def list_models(cursor):
    cursor.execute(
        "SELECT model_key, model_type, display_name, valid_durations, available, notes "
        "FROM model_registry ORDER BY model_type, model_key"
    )
    print(f"{'KEY':<18} {'TYPE':<7} {'AVAILABLE':<10} {'DURATIONS':<15} NOTES")
    for key, mtype, name, durations, available, notes in cursor.fetchall():
        dur_str = ",".join(str(d) for d in json.loads(durations)) + "s" if durations else "-"
        avail_str = "yes" if available else "NO (not downloaded)"
        print(f"{key:<18} {mtype:<7} {avail_str:<10} {dur_str:<15} {notes or ''}")


def get_model(cursor, model_key: str):
    cursor.execute(
        "SELECT model_type, valid_durations, available FROM model_registry WHERE model_key = %s",
        (model_key,),
    )
    row = cursor.fetchone()
    if not row:
        raise SystemExit(f"Unknown model_key '{model_key}'. Run --list to see available models.")
    model_type, valid_durations, available = row
    return {
        "model_type": model_type,
        "valid_durations": json.loads(valid_durations) if valid_durations else None,
        "available": bool(available),
    }


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        list_models(cursor)
        cursor.close()
        conn.close()
        return

    force = "--force" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--force"]

    if len(args) != 4:
        print("Usage: python set_project_models.py PROJECT_ID IMAGE_MODEL_KEY VIDEO_MODEL_KEY DURATION_SECONDS [--force]")
        print("       python set_project_models.py --list")
        sys.exit(1)

    project_id, image_model_key, video_model_key, duration_str = args
    duration = int(duration_str)

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        image_model = get_model(cursor, image_model_key)
        if image_model["model_type"] != "image":
            raise SystemExit(f"'{image_model_key}' is registered as a {image_model['model_type']} model, not image.")
        if not image_model["available"]:
            print(f"WARNING: '{image_model_key}' is registered but marked NOT downloaded/available yet.")

        video_model = get_model(cursor, video_model_key)
        if video_model["model_type"] != "video":
            raise SystemExit(f"'{video_model_key}' is registered as a {video_model['model_type']} model, not video.")
        if not video_model["available"]:
            print(f"WARNING: '{video_model_key}' is registered but marked NOT downloaded/available yet.")

        if video_model["valid_durations"] and duration not in video_model["valid_durations"]:
            raise SystemExit(
                f"{duration}s is not a valid duration for '{video_model_key}'. "
                f"Valid options: {video_model['valid_durations']}"
            )

        cursor.execute("SELECT image_model_key, video_model_key, locked_duration_seconds "
                        "FROM project_model_config WHERE project_id = %s", (project_id,))
        existing = cursor.fetchone()

        if existing and not force:
            print(f"Project '{project_id}' already has a LOCKED config: "
                  f"image={existing[0]}, video={existing[1]}, duration={existing[2]}s.")
            print("Pass --force to change it (this affects consistency for every future shot in this project).")
            sys.exit(1)

        cursor.execute(
            """
            INSERT INTO project_model_config (project_id, image_model_key, video_model_key, locked_duration_seconds)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                image_model_key=VALUES(image_model_key), video_model_key=VALUES(video_model_key),
                locked_duration_seconds=VALUES(locked_duration_seconds)
            """,
            (project_id, image_model_key, video_model_key, duration),
        )
        conn.commit()
        print(f"Locked '{project_id}': image={image_model_key}, video={video_model_key}, duration={duration}s.")
        if existing and force:
            print("NOTE: this OVERWRITES the previous lock. Shots already generated under the old "
                  "config are not retroactively changed — only future generation uses the new values.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
