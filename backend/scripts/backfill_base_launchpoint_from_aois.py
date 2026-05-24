"""One-time backfill: project every AOI tagged with ``metadata.aoi_kind`` into
its matching Neo4j ``Base`` / ``LaunchPoint`` / ``Facility`` mirror node.

After Phase 1.D the AOI POST/PATCH endpoints project on write, but pre-existing
rows (created before the projector existed, or seeded via SQL) need a one-pass
catch-up. Re-runnable — every MERGE is idempotent.

Usage::

    python -m backend.scripts.backfill_base_launchpoint_from_aois [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import postgis_db  # noqa: E402
from graph_writes import merge_site_from_aoi  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report rows; do not write to Neo4j")
    args = parser.parse_args()

    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, metadata,
                   ST_Y(ST_Centroid(geom)) AS centroid_lat,
                   ST_X(ST_Centroid(geom)) AS centroid_lon
            FROM aois
            WHERE metadata ? 'aoi_kind'
              AND metadata->>'aoi_kind' IN ('base', 'launchpoint', 'launch_point', 'facility')
            ORDER BY id
            """,
        )
        rows = [dict(r) for r in cursor.fetchall()]

    print(f"backfill_base_launchpoint_from_aois: {len(rows)} candidate AOIs found")
    if args.dry_run:
        for row in rows:
            metadata = row["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            print(f"  - aoi_id={row['id']} kind={metadata.get('aoi_kind')} name={row['name']}")
        return 0

    written = 0
    skipped = 0
    for row in rows:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        kind = metadata.get("aoi_kind")
        element_id = merge_site_from_aoi(
            aoi_postgis_id=row["id"],
            kind=kind,
            name=row["name"],
            latitude=row["centroid_lat"],
            longitude=row["centroid_lon"],
            metadata=metadata,
        )
        if element_id is None:
            skipped += 1
            print(f"  SKIP aoi_id={row['id']} (kind={kind} not projectable)")
        else:
            written += 1
            print(f"  OK   aoi_id={row['id']} -> {element_id}")

    print(f"backfill_base_launchpoint_from_aois: wrote={written}, skipped={skipped}")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
