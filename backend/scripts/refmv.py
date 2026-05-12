"""One-shot: re-run telemetry extraction for an existing fmv_clips row.

Usage (from inside the backend container):
    python /app/scripts/refmv.py <clip_id>

Deletes existing fmv_frames rows for the clip and reinserts using the
current `extract_telemetry` implementation. Used to refresh data that was
stored before the KLV parser fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app")

from database import postgis_db
from video_metadata import extract_telemetry


def main(clip_id: int) -> None:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT file_path, duration_seconds, fps FROM fmv_clips WHERE id = %s",
            (clip_id,),
        )
        row = cur.fetchone()
        if not row:
            print(f"clip {clip_id} not found", file=sys.stderr)
            sys.exit(1)
        # psycopg2 returns either tuple or dict-like row depending on cursor type;
        # access via index for portability.
        file_path = row["file_path"] if hasattr(row, "keys") else row[0]
        duration = row["duration_seconds"] if hasattr(row, "keys") else row[1]
        fps = row["fps"] if hasattr(row, "keys") else row[2]

        video_path = Path(file_path)
        if not video_path.exists():
            print(f"video not found at {video_path}", file=sys.stderr)
            sys.exit(2)

        rows = extract_telemetry(video_path, clip_id, float(duration or 0), float(fps or 30))
        cur.execute("DELETE FROM fmv_frames WHERE clip_id = %s", (clip_id,))
        for r in rows:
            cur.execute(
                "INSERT INTO fmv_frames (clip_id, frame_index, timestamp_seconds, telemetry, footprint) "
                "VALUES (%s, %s, %s, %s::jsonb, ST_GeomFromText(%s, 4326))",
                r,
            )
        print(f"re-extracted {len(rows)} frames for clip {clip_id}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(64)
    main(int(sys.argv[1]))
