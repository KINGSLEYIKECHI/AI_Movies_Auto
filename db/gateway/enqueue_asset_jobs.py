r"""
Populate the jobs table for the asset-generation pipeline: character/
location/prop REFERENCE images (generate once, reuse everywhere that
entity appears), then per-shot images, then per-shot video.

Video jobs are HARD-GATED: enqueue_shot_videos refuses to queue ANY video
job for the project until EVERY shot image in that entire project is
'done' — not just the current episode/scene. This is deliberate: catching
a bad reference or shot image before committing GPU time to video is the
whole point of the manual-review checkpoint this creates.

Usage:
    python enqueue_asset_jobs.py PROJECT_ID references
    python enqueue_asset_jobs.py PROJECT_ID shot-images
    python enqueue_asset_jobs.py PROJECT_ID shot-videos
    python enqueue_asset_jobs.py PROJECT_ID status
"""

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

BASE_PATH = Path(os.getenv("PROJECTS_BASE_PATH", "/ai_movies"))


def _job_exists(cursor, project_id, job_type, entity_col, entity_id):
    cursor.execute(
        f"SELECT id FROM jobs WHERE project_id = %s AND job_type = %s AND {entity_col} = %s",
        (project_id, job_type, entity_id),
    )
    return cursor.fetchone() is not None


def enqueue_references(cursor, project_id: str) -> int:
    """One reference-image job per character/location/prop that doesn't
    already have one — this is the 'lock the look once' mechanism."""
    count = 0

    cursor.execute("SELECT character_id, name, appearance FROM characters WHERE project_id = %s", (project_id,))
    for character_id, name, appearance in cursor.fetchall():
        if _job_exists(cursor, project_id, "character_reference", "character_id", character_id):
            continue
        prompt = f"Reference portrait of {name}. {appearance or ''}"
        out_path = str(BASE_PATH / project_id / "assets" / "characters" / f"{character_id.split('__')[-1]}.png")
        cursor.execute(
            "INSERT INTO jobs (project_id, character_id, job_type, prompt, output_path, status) "
            "VALUES (%s, %s, 'character_reference', %s, %s, 'queued')",
            (project_id, character_id, prompt, out_path),
        )
        count += 1

    cursor.execute("SELECT location_id, name, architecture, atmosphere FROM locations WHERE project_id = %s", (project_id,))
    for location_id, name, architecture, atmosphere in cursor.fetchall():
        if _job_exists(cursor, project_id, "location_reference", "location_id", location_id):
            continue
        prompt = f"Reference establishing shot of {name}. {architecture or ''} {atmosphere or ''}"
        out_path = str(BASE_PATH / project_id / "assets" / "locations" / f"{location_id.split('__')[-1]}.png")
        cursor.execute(
            "INSERT INTO jobs (project_id, location_id, job_type, prompt, output_path, status) "
            "VALUES (%s, %s, 'location_reference', %s, %s, 'queued')",
            (project_id, location_id, prompt, out_path),
        )
        count += 1

    cursor.execute("SELECT prop_id, name, appearance FROM props WHERE project_id = %s", (project_id,))
    for prop_id, name, appearance in cursor.fetchall():
        if _job_exists(cursor, project_id, "prop_reference", "prop_id", prop_id):
            continue
        prompt = f"Reference image of {name}. {appearance or ''}"
        out_path = str(BASE_PATH / project_id / "assets" / "props" / f"{prop_id.split('__')[-1]}.png")
        cursor.execute(
            "INSERT INTO jobs (project_id, prop_id, job_type, prompt, output_path, status) "
            "VALUES (%s, %s, 'prop_reference', %s, %s, 'queued')",
            (project_id, prop_id, prompt, out_path),
        )
        count += 1

    return count


def enqueue_shot_images(cursor, project_id: str) -> int:
    """One shot-image job per shot that doesn't already have one."""
    cursor.execute(
        """
        SELECT s.shot_id, s.scene_id, s.image_prompt
        FROM shots s
        JOIN scene_plan sp ON sp.scene_id = s.scene_id
        WHERE sp.project_id = %s
        """,
        (project_id,),
    )
    count = 0
    for shot_id, scene_id, image_prompt in cursor.fetchall():
        if _job_exists(cursor, project_id, "shot_image", "shot_id", shot_id):
            continue
        episode_id = scene_id.split("_SC_")[0]
        short_shot = shot_id.split("__")[-1]
        out_path = str(BASE_PATH / project_id / "episodes" / episode_id.split("__")[-1] / "images" / f"{short_shot}.png")
        cursor.execute(
            "INSERT INTO jobs (project_id, scene_id, shot_id, job_type, prompt, output_path, status) "
            "VALUES (%s, %s, %s, 'shot_image', %s, %s, 'queued')",
            (project_id, scene_id, shot_id, image_prompt, out_path),
        )
        count += 1
    return count


def shot_image_completion(cursor, project_id: str):
    """Returns (total_shots, done_shot_images) for the whole project."""
    cursor.execute(
        """
        SELECT s.shot_id
        FROM shots s
        JOIN scene_plan sp ON sp.scene_id = s.scene_id
        WHERE sp.project_id = %s
        """,
        (project_id,),
    )
    all_shots = {row[0] for row in cursor.fetchall()}

    cursor.execute(
        "SELECT shot_id FROM jobs WHERE project_id = %s AND job_type = 'shot_image' AND status = 'done'",
        (project_id,),
    )
    done_shots = {row[0] for row in cursor.fetchall()}

    return len(all_shots), len(all_shots & done_shots)


def enqueue_shot_videos(cursor, project_id: str) -> int:
    """THE GATE: refuses to queue any video job until every shot image in
    the whole project is done — not just the current scene/episode."""
    total, done = shot_image_completion(cursor, project_id)
    if total == 0:
        print(f"No shots found for '{project_id}' at all — run enqueue_shot_images first.")
        return 0
    if done < total:
        print(f"BLOCKED: {done}/{total} shot images done for '{project_id}'. "
              f"All shot images must complete before ANY video job can be queued.")
        return 0

    cursor.execute(
        """
        SELECT s.shot_id, s.scene_id, s.video_prompt, j.output_path
        FROM shots s
        JOIN scene_plan sp ON sp.scene_id = s.scene_id
        JOIN jobs j ON j.shot_id = s.shot_id AND j.job_type = 'shot_image'
        WHERE sp.project_id = %s
        """,
        (project_id,),
    )
    count = 0
    for shot_id, scene_id, video_prompt, source_image_path in cursor.fetchall():
        if _job_exists(cursor, project_id, "shot_video", "shot_id", shot_id):
            continue
        episode_id = scene_id.split("_SC_")[0]
        short_shot = shot_id.split("__")[-1]
        out_path = str(BASE_PATH / project_id / "episodes" / episode_id.split("__")[-1] / "clips" / f"{short_shot}.mp4")
        # source_image_path is embedded in the prompt field so the (future)
        # broker knows which completed image to feed in for image-to-video —
        # the actual FK link is job_type='shot_image' + shot_id, this is
        # just a convenience for a quick read.
        prompt = f"[source_image: {source_image_path}] {video_prompt}"
        cursor.execute(
            "INSERT INTO jobs (project_id, scene_id, shot_id, job_type, prompt, output_path, status) "
            "VALUES (%s, %s, %s, 'shot_video', %s, %s, 'queued')",
            (project_id, scene_id, shot_id, prompt, out_path),
        )
        count += 1

    print(f"All {total} shot images done — queued {count} video jobs.")
    return count


def print_status(cursor, project_id: str):
    cursor.execute(
        "SELECT job_type, status, COUNT(*) FROM jobs WHERE project_id = %s "
        "GROUP BY job_type, status ORDER BY job_type, status",
        (project_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        print(f"No jobs queued yet for '{project_id}'.")
        return
    print(f"{'JOB TYPE':<22} {'STATUS':<10} COUNT")
    for job_type, status, count in rows:
        print(f"{job_type:<22} {status:<10} {count}")

    total, done = shot_image_completion(cursor, project_id)
    print(f"\nShot images: {done}/{total} done."
          + (" Video jobs may be queued." if total and done == total else " Video jobs BLOCKED until all shot images are done."))


def main():
    if len(sys.argv) != 3:
        print("Usage: python enqueue_asset_jobs.py PROJECT_ID {references|shot-images|shot-videos|status}")
        sys.exit(1)

    project_id, action = sys.argv[1], sys.argv[2]

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        if action == "references":
            n = enqueue_references(cursor, project_id)
            conn.commit()
            print(f"Queued {n} new reference jobs (character/location/prop) for '{project_id}'.")
        elif action == "shot-images":
            n = enqueue_shot_images(cursor, project_id)
            conn.commit()
            print(f"Queued {n} new shot-image jobs for '{project_id}'.")
        elif action == "shot-videos":
            enqueue_shot_videos(cursor, project_id)
            conn.commit()
        elif action == "status":
            print_status(cursor, project_id)
        else:
            print(f"Unknown action '{action}'. Use references, shot-images, shot-videos, or status.")
            sys.exit(1)
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
