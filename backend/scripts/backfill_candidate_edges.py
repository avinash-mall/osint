"""One-time backfill: persist `CANDIDATE_DETECTED_AS` for every pending row in
``detection_target_candidates``.

Before the Link Graph redesign, candidate edges were synthesised in-memory by
``routers/graph.py`` on every request. After Phase 1.B they are persisted on
candidate creation. Existing installs have rows that pre-date the change —
this script walks them once and MERGEs the corresponding Neo4j edge.

Idempotent: re-running is safe (``MERGE`` is the underlying op). Run via::

    python -m backend.scripts.backfill_candidate_edges [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow ``python backend/scripts/backfill_candidate_edges.py`` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import postgis_db  # noqa: E402
from graph_writes import merge_candidate_detected_as  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Process at most N rows (0 = no limit)")
    parser.add_argument("--dry-run", action="store_true", help="Report counts; do not write to Neo4j")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    sql = """
        SELECT c.id AS candidate_id, c.detection_id, c.target_id, c.score, c.reason,
               d.class AS det_class, d.confidence AS det_confidence,
               ST_X(d.centroid) AS lon, ST_Y(d.centroid) AS lat
        FROM detection_target_candidates c
        JOIN detections d ON d.id = c.detection_id
        WHERE c.status = 'pending'
        ORDER BY c.id
    """
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"

    with postgis_db.get_cursor() as cursor:
        cursor.execute(sql)
        rows = [dict(r) for r in cursor.fetchall()]

    print(f"backfill_candidate_edges: {len(rows)} pending row(s) found")
    if args.dry_run:
        return 0

    written = 0
    skipped = 0
    for row in rows:
        ok = merge_candidate_detected_as(
            detection_id=row["detection_id"],
            detection_class=row["det_class"],
            detection_confidence=row["det_confidence"],
            detection_lat=row["lat"],
            detection_lon=row["lon"],
            target_id=row["target_id"],
            candidate_id=row["candidate_id"],
            score=row["score"],
            reason=row["reason"],
        )
        if ok:
            written += 1
        else:
            skipped += 1
            logger.warning(
                "skipped candidate_id=%s (target=%s) — Target not found in Neo4j",
                row["candidate_id"],
                row["target_id"],
            )

    print(f"backfill_candidate_edges: wrote={written}, skipped={skipped}")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
