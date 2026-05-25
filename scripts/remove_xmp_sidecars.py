"""
One-shot cleanup: hard-unlink every .xmp sidecar KaMeRa wrote.

Scope: only files whose path is recorded in `images.xmp_sidecar_path`. That
restriction is the safety net — it guarantees we only delete sidecars our
own decision flow created, never user-authored XMP from Lightroom / Capture
One / Bridge sitting next to RAWs in the original ingest folder.

After deletion, sets `xmp_sidecar_path` to NULL on every row so the column
becomes consistent before the v42 migration drops it.

Usage:
    venv/bin/python scripts/remove_xmp_sidecars.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "pca.db"


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, xmp_sidecar_path FROM images "
        "WHERE xmp_sidecar_path IS NOT NULL AND xmp_sidecar_path != ''"
    ).fetchall()

    print(f"Found {len(rows)} sidecar paths in DB.")

    deleted = 0
    already_gone = 0
    failed: list[tuple[int, str, str]] = []

    for row in rows:
        path = Path(row["xmp_sidecar_path"])
        if not path.exists():
            already_gone += 1
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            failed.append((row["id"], str(path), str(exc)))

    conn.execute(
        "UPDATE images SET xmp_sidecar_path = NULL "
        "WHERE xmp_sidecar_path IS NOT NULL"
    )
    conn.commit()
    conn.close()

    print(f"Deleted:      {deleted}")
    print(f"Already gone: {already_gone}")
    print(f"Failed:       {len(failed)}")
    for image_id, path, err in failed:
        print(f"  image_id={image_id}  path={path}  error={err}")

    print("Nulled xmp_sidecar_path on all rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
