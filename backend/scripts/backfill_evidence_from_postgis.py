"""One-pass backfill: project every pre-existing FMV clip / Document with
extracted_entities / Observation with entity_id into Neo4j.

After Phase 2.B–D land, new rows auto-project via Celery tasks. Existing
rows from before the projectors existed need this one-time catchup. The
script calls projection helpers directly so it works without a running
Celery worker. Idempotent — every MERGE is keyed on ``postgis_id``.

Usage::

    python -m backend.scripts.backfill_evidence_from_postgis [--limit-fmv N] [--limit-docs N] [--limit-observations N] [--dry-run]

Counts per bucket are reported on stdout; non-zero exit if any bucket had
a write failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import postgis_db  # noqa: E402
from graph_writes import (  # noqa: E402
    load_entity_label_index,
    project_document_with_mentions,
    project_fmv_clip_and_tracks,
    project_observation_batch,
)


def backfill_fmv(limit: int | None) -> tuple[int, int]:
    """Project every fmv_clips row. Returns (clips_written, tracks_written)."""
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, name, duration_seconds, fps, width, height
            FROM fmv_clips
            ORDER BY id
            {f'LIMIT {int(limit)}' if limit else ''}
            """
        )
        clips = [dict(r) for r in cursor.fetchall()]

    clips_written = 0
    tracks_written = 0
    for clip in clips:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT (metadata->>'track_id') AS track_uid,
                       class AS cls,
                       MAX(confidence)::float AS confidence,
                       MIN(frame_index) AS first_frame,
                       MAX(frame_index) AS last_frame
                FROM fmv_detections
                WHERE clip_id = %s
                  AND deleted_at IS NULL
                  AND (metadata->>'consolidated')::boolean IS TRUE
                  AND metadata ? 'track_id'
                GROUP BY (metadata->>'track_id'), class
                """,
                (clip["id"],),
            )
            track_rows = [dict(r) for r in cursor.fetchall()]
        by_uid: dict[str, dict] = {}
        for row in track_rows:
            uid = row.get("track_uid")
            if not uid:
                continue
            existing = by_uid.get(uid)
            if existing is None or (row.get("confidence") or 0) > (existing.get("confidence") or 0):
                by_uid[uid] = row
        tracks = list(by_uid.values())
        counts = project_fmv_clip_and_tracks(
            clip_id=clip["id"],
            clip_name=clip.get("name") or f"clip-{clip['id']}",
            duration_seconds=clip.get("duration_seconds"),
            fps=clip.get("fps"),
            width=clip.get("width"),
            height=clip.get("height"),
            tracks=tracks,
        )
        clips_written += counts.get("clip", 0)
        tracks_written += counts.get("tracks", 0)
    return clips_written, tracks_written


def backfill_documents(limit: int | None) -> tuple[int, int]:
    """Project every documents row with non-empty extracted_entities."""
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, title, media_type, summary, extracted_entities
            FROM documents
            WHERE extracted_entities IS NOT NULL
              AND jsonb_array_length(coalesce(extracted_entities, '[]'::jsonb)) > 0
            ORDER BY id
            {f'LIMIT {int(limit)}' if limit else ''}
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]

    index = load_entity_label_index() if rows else None
    docs_written = 0
    mentions_written = 0
    for row in rows:
        extracted = row.get("extracted_entities") or []
        if isinstance(extracted, str):
            try:
                extracted = json.loads(extracted)
            except json.JSONDecodeError:
                extracted = []
        counts = project_document_with_mentions(
            document_id=row["id"],
            title=row.get("title") or f"doc-{row['id']}",
            media_type=row.get("media_type"),
            summary=row.get("summary"),
            extracted_entities=extracted,
            entity_label_index=index,
        )
        docs_written += counts.get("document", 0)
        mentions_written += counts.get("mentions", 0)
    return docs_written, mentions_written


def backfill_observations(limit: int | None, batch_size: int = 200) -> int:
    """Project every observations row with entity_id, batched."""
    total = 0
    offset = 0
    while True:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id AS postgis_id, entity_id, event_type, title, confidence,
                       observed_at::text AS observed_at,
                       ST_Y(geom) AS latitude, ST_X(geom) AS longitude
                FROM observations
                WHERE entity_id IS NOT NULL
                ORDER BY id
                OFFSET %s LIMIT %s
                """,
                (offset, batch_size),
            )
            rows = [dict(r) for r in cursor.fetchall()]
        if not rows:
            break
        total += project_observation_batch(rows)
        if limit and total >= limit:
            return total
        if len(rows) < batch_size:
            break
        offset += batch_size
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-fmv", type=int, default=0)
    parser.add_argument("--limit-docs", type=int, default=0)
    parser.add_argument("--limit-observations", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="Report row counts, no writes")
    args = parser.parse_args()

    if args.dry_run:
        # Just report how many candidates exist.
        with postgis_db.get_cursor() as cursor:
            cursor.execute("SELECT count(*) AS c FROM fmv_clips")
            n_clips = (cursor.fetchone() or {}).get("c", 0)
            cursor.execute(
                "SELECT count(*) AS c FROM documents WHERE jsonb_array_length(coalesce(extracted_entities, '[]'::jsonb)) > 0"
            )
            n_docs = (cursor.fetchone() or {}).get("c", 0)
            cursor.execute("SELECT count(*) AS c FROM observations WHERE entity_id IS NOT NULL")
            n_obs = (cursor.fetchone() or {}).get("c", 0)
        print(f"dry run: fmv_clips={n_clips} documents_with_entities={n_docs} observations_with_entity_id={n_obs}")
        return 0

    print("backfill_evidence_from_postgis: starting")
    clips, tracks = backfill_fmv(args.limit_fmv or None)
    print(f"  FMV: clips={clips} tracks={tracks}")
    docs, mentions = backfill_documents(args.limit_docs or None)
    print(f"  Documents: docs={docs} mentions={mentions}")
    observations = backfill_observations(args.limit_observations or None)
    print(f"  Observations: projected={observations}")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
