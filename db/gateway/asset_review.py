"""Manage the small human-review gate for canonical visual assets.

Usage:
  python asset_review.py PROJECT_ID list [candidate|approved|rejected]
  python asset_review.py PROJECT_ID approve ASSET_ID
  python asset_review.py PROJECT_ID reject ASSET_ID [REASON]
"""
import os
import sys
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
DB_CONFIG = {"host": os.getenv("MYSQL_HOST", "mysql"), "port": int(os.getenv("MYSQL_PORT", "3306")),
             "user": os.getenv("MYSQL_USER", "glm_user"), "password": os.getenv("MYSQL_PASSWORD", "changeme"),
             "database": os.getenv("MYSQL_DATABASE", "glm_pipeline")}


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    project_id, action = sys.argv[1:3]
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        if action == "list":
            status = sys.argv[3] if len(sys.argv) > 3 else "candidate"
            cur.execute("SELECT id, asset_type, entity_id, output_path, generation_model, created_at "
                        "FROM asset_records WHERE project_id=%s AND status=%s ORDER BY id", (project_id, status))
            for row in cur.fetchall():
                print("\t".join(str(x or "") for x in row))
        elif action in ("approve", "reject") and len(sys.argv) >= 4:
            asset_id = int(sys.argv[3])
            new_status = "approved" if action == "approve" else "rejected"
            cur.execute("UPDATE asset_records SET status=%s, reviewed_at=NOW() WHERE id=%s AND project_id=%s",
                        (new_status, asset_id, project_id))
            if cur.rowcount != 1:
                raise SystemExit(f"Asset {asset_id} was not found in project {project_id}.")
            conn.commit()
            print(f"Asset {asset_id} marked {new_status}.")
        else:
            raise SystemExit(__doc__)
    finally:
        cur.close(); conn.close()


if __name__ == "__main__":
    main()
