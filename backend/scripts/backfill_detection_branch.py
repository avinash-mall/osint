"""Backfill detections.metadata with normalized ontology fields.

Step 4 of the ontology refactor plan
(/home/avinash/.claude/plans/the-inference-system-has-piped-nest.md).

For every row in the ``detections`` table this script:

1. Reads ``detections.class`` and ``detections.metadata``.
2. Picks the source label: ``metadata.original_class`` if present, else
   ``detections.class``.
3. Calls :func:`backend.ontology.normalize` with the ``source_layer``
   metadata (empty string if absent).
4. Patches metadata with the new ``branch_id``, ``icon_key``,
   ``canonical_label``, ``was_unknown`` and ``ontology_object_id`` keys
   without disturbing existing fields.
5. UPDATEs the row with the new metadata JSON.

Usage:
    python -m backend.scripts.backfill_detection_branch
    python -m backend.scripts.backfill_detection_branch --dry-run
    python -m backend.scripts.backfill_detection_branch --batch-size 1000
    python -m backend.scripts.backfill_detection_branch --skip-existing
    python -m backend.scripts.backfill_detection_branch --where "created_at > '2026-05-01'"

Notes:
- Rows are processed in deterministic ``id ASC`` order so re-runs are
  idempotent.
- ``--dry-run`` still calls :func:`normalize` (which UPSERTs into
  ``ontology_unknown_labels`` for unknown labels). It just skips the
  UPDATE on ``detections``. Unknown counters may therefore be incremented
  in dry-run as well -- that is intentional and documented here.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

# Allow running as `python -m backend.scripts.backfill_detection_branch`.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# backend/ontology.py uses `from database import postgis_db`, which resolves
# only if backend/ is also on sys.path.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.database import postgis_db  # noqa: E402
from backend import ontology  # noqa: E402

logger = logging.getLogger("backfill_detection_branch")


PATCH_KEYS = (
    "branch_id",
    "icon_key",
    "canonical_label",
    "was_unknown",
    "ontology_object_id",
)


def _coerce_metadata(meta) -> dict:
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return dict(meta)
    if isinstance(meta, str):
        try:
            parsed = json.loads(meta)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _build_patch(label: str, layer: str) -> dict:
    norm = ontology.normalize(label, layer=layer or "")
    return {
        "branch_id": norm.branch_id,
        "icon_key": norm.icon_key,
        "canonical_label": norm.canonical_label,
        "was_unknown": bool(norm.was_unknown),
        "ontology_object_id": norm.ontology_object_id,
    }


def _build_select_sql(where: str | None, skip_existing: bool) -> tuple[str, str]:
    """Return (select_sql, count_sql) honoring optional WHERE / skip flags."""
    clauses: list[str] = []
    if where:
        clauses.append(f"({where})")
    if skip_existing:
        clauses.append("NOT (metadata ? 'branch_id')")
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    select_sql = (
        "SELECT id, class, metadata FROM detections "
        f"{where_sql} ORDER BY id ASC"
    )
    count_sql = f"SELECT count(*) AS n FROM detections {where_sql}"
    return select_sql, count_sql


def _commit_batch(batch: list[tuple[int, str]]) -> None:
    if not batch:
        return
    with postgis_db.get_cursor(commit=True) as cur:
        for det_id, meta_json in batch:
            cur.execute(
                "UPDATE detections SET metadata = %s::jsonb WHERE id = %s",
                (meta_json, det_id),
            )


def backfill(
    *,
    dry_run: bool = False,
    batch_size: int = 500,
    skip_existing: bool = False,
    where: str | None = None,
) -> dict:
    """Iterate detections and patch metadata. Returns summary counters."""
    # Warm the ontology cache once so the per-row path stays cheap.
    ontology._get_tree()

    select_sql, count_sql = _build_select_sql(where, skip_existing)

    with postgis_db.get_cursor() as cur:
        cur.execute(count_sql)
        total = int((cur.fetchone() or {}).get("n") or 0)
    logger.info(
        "[backfill] start total=%d batch=%d dry_run=%s skip_existing=%s where=%r",
        total, batch_size, dry_run, skip_existing, where,
    )

    processed = 0
    updated = 0
    unknown = 0
    skipped_unchanged = 0
    branch_counts: Counter[str] = Counter()
    pending: list[tuple[int, str]] = []
    start = time.time()

    # Use a server-side cursor to avoid loading the whole table.
    conn = postgis_db.get_connection()
    try:
        ss_name = f"backfill_det_{int(start * 1000)}"
        ss_cur = conn.cursor(name=ss_name)
        try:
            ss_cur.itersize = max(50, min(batch_size, 5000))
            ss_cur.execute(select_sql)
            for row in ss_cur:
                # Server-side cursors on the default conn use tuples.
                det_id, det_class, det_meta = row[0], row[1], row[2]
                meta = _coerce_metadata(det_meta)

                source_label = meta.get("original_class") or det_class or ""
                source_layer = meta.get("source_layer") or ""
                patch = _build_patch(str(source_label), str(source_layer))
                if patch["was_unknown"]:
                    unknown += 1
                branch_counts[patch["branch_id"]] += 1

                # Check if anything actually changes.
                changed = any(meta.get(k) != patch[k] for k in PATCH_KEYS)
                if changed:
                    new_meta = dict(meta)
                    new_meta.update(patch)
                    if not dry_run:
                        pending.append((det_id, json.dumps(new_meta)))
                    updated += 1
                else:
                    skipped_unchanged += 1

                processed += 1

                if not dry_run and len(pending) >= batch_size:
                    _commit_batch(pending)
                    pending.clear()
                    elapsed = time.time() - start
                    logger.info(
                        "[backfill] processed=%d updated=%d unknown=%d elapsed=%.1fs",
                        processed, updated, unknown, elapsed,
                    )
                elif processed % batch_size == 0:
                    elapsed = time.time() - start
                    logger.info(
                        "[backfill] processed=%d updated=%d unknown=%d elapsed=%.1fs",
                        processed, updated, unknown, elapsed,
                    )
        finally:
            try:
                ss_cur.close()
            except Exception:
                pass
        # Server-side cursor only requires a rollback to release.
        conn.rollback()
    finally:
        postgis_db.put_connection(conn)

    if not dry_run and pending:
        _commit_batch(pending)
        pending.clear()

    elapsed = time.time() - start
    logger.info(
        "[backfill] done total=%d processed=%d updated=%d unchanged=%d unknown=%d elapsed=%.1fs",
        total, processed, updated, skipped_unchanged, unknown, elapsed,
    )

    print("")
    print("Per-branch summary (post-normalize):")
    for branch_id, n in sorted(branch_counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {branch_id:32s} {n:>6d}")
    print("")
    print(
        f"total={total} processed={processed} updated={updated} "
        f"unchanged={skipped_unchanged} unknown={unknown} "
        f"dry_run={dry_run} elapsed={elapsed:.1f}s"
    )

    return {
        "total": total,
        "processed": processed,
        "updated": updated,
        "unchanged": skipped_unchanged,
        "unknown": unknown,
        "branch_counts": dict(branch_counts),
        "elapsed_seconds": elapsed,
    }


def main() -> int:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    parser = argparse.ArgumentParser(
        description="Backfill detections.metadata with ontology normalizer output.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute everything but do not write detections rows. "
             "Note: unknown labels are still UPSERTed into "
             "ontology_unknown_labels by normalize().",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="Commit every N rows (default 500).",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip rows whose metadata already has branch_id.",
    )
    parser.add_argument(
        "--where", type=str, default=None,
        help="Optional WHERE clause to limit scope (no leading 'WHERE').",
    )
    args = parser.parse_args()

    backfill(
        dry_run=args.dry_run,
        batch_size=max(1, int(args.batch_size)),
        skip_existing=args.skip_existing,
        where=args.where,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
