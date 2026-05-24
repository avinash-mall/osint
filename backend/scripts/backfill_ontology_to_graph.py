"""One-pass Phase 3 backfill: ontology branches + objects + UnknownLabels +
LABEL_OF edges from Detection.class.

Phase 3.A's on-write hooks fire only on new writes. This script catches up
any installation that pre-dates them. Each step is idempotent.

Usage::

    python -m backend.scripts.backfill_ontology_to_graph [--skip-labels] [--skip-unknown] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import postgis_db  # noqa: E402
import ontology as ontology_module  # noqa: E402
from graph_writes import (  # noqa: E402
    project_label_of_for_detection_class,
    project_ontology_branches_and_objects,
    project_unknown_label,
)


def backfill_ontology() -> dict:
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, parent_id, label, color, short, icon_key, order_index
            FROM ontology_branches ORDER BY order_index ASC, id ASC
            """
        )
        branches = [dict(r) for r in cursor.fetchall()]
        cursor.execute(
            """
            SELECT id, branch_id, label, prompt, icon_key, order_index
            FROM ontology_objects ORDER BY order_index ASC, id ASC
            """
        )
        objects = [dict(r) for r in cursor.fetchall()]
    return project_ontology_branches_and_objects(branches=branches, objects=objects)


def backfill_unknown_labels(supports_limit: int = 5) -> int:
    projected = 0
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT label, layer, count, first_seen::text AS first_seen,
                   last_seen::text AS last_seen, suggested_branch_id
            FROM ontology_unknown_labels
            ORDER BY count DESC NULLS LAST, label
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]
    for row in rows:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM detections
                WHERE class = %s AND deleted_at IS NULL
                ORDER BY created_at DESC LIMIT %s
                """,
                (row["label"], supports_limit),
            )
            support_ids = [int(r["id"]) for r in cursor.fetchall()]
        ok = project_unknown_label(
            label=row["label"],
            layer=row.get("layer"),
            count=int(row.get("count") or 0),
            first_seen=row.get("first_seen"),
            last_seen=row.get("last_seen"),
            suggested_branch_id=row.get("suggested_branch_id"),
            supporting_detection_ids=support_ids,
        )
        if ok:
            projected += 1
    return projected


def backfill_label_of(batch_size: int = 500) -> int:
    total = 0
    offset = 0
    while True:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, class FROM detections
                WHERE deleted_at IS NULL
                ORDER BY id OFFSET %s LIMIT %s
                """,
                (offset, batch_size),
            )
            rows = [dict(r) for r in cursor.fetchall()]
        if not rows:
            break
        by_object: dict[str, list[int]] = {}
        by_object_class: dict[str, str] = {}
        for row in rows:
            try:
                norm = ontology_module.normalize(row["class"])
            except Exception:
                continue
            object_id = getattr(norm, "object_id", None) if norm else None
            if not object_id:
                continue
            by_object.setdefault(object_id, []).append(int(row["id"]))
            by_object_class.setdefault(object_id, str(row["class"]))
        for object_id, det_ids in by_object.items():
            total += project_label_of_for_detection_class(
                detection_class=by_object_class[object_id],
                ontology_object_id=object_id,
                detection_postgis_ids=det_ids,
            )
        if len(rows) < batch_size:
            break
        offset += batch_size
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-labels", action="store_true", help="Skip LABEL_OF backfill (slowest step)")
    parser.add_argument("--skip-unknown", action="store_true", help="Skip UnknownLabel backfill")
    parser.add_argument("--dry-run", action="store_true", help="Report counts only")
    args = parser.parse_args()

    if args.dry_run:
        with postgis_db.get_cursor() as cursor:
            cursor.execute("SELECT count(*) AS c FROM ontology_branches")
            n_b = (cursor.fetchone() or {}).get("c", 0)
            cursor.execute("SELECT count(*) AS c FROM ontology_objects")
            n_o = (cursor.fetchone() or {}).get("c", 0)
            cursor.execute("SELECT count(*) AS c FROM ontology_unknown_labels")
            n_u = (cursor.fetchone() or {}).get("c", 0)
            cursor.execute("SELECT count(*) AS c FROM detections WHERE deleted_at IS NULL")
            n_d = (cursor.fetchone() or {}).get("c", 0)
        print(f"dry run: branches={n_b} objects={n_o} unknown_labels={n_u} detections={n_d}")
        return 0

    print("backfill_ontology_to_graph: starting")
    counts = backfill_ontology()
    print(f"  Ontology: {counts}")
    if not args.skip_unknown:
        n = backfill_unknown_labels()
        print(f"  UnknownLabels: projected={n}")
    if not args.skip_labels:
        n = backfill_label_of()
        print(f"  LABEL_OF edges: written={n}")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
