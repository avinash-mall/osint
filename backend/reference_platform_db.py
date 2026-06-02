"""Helpers for reading and writing rows in the Reference Embedding DB.

Pure SQL wrappers; no HTTP, no file I/O. See:
- docs/backend/reference-platform-db.md for the schema this module manipulates
- docs/backend/reference-platform-baker.md for the bake script that drives it

All functions expect callers to hold a live cursor from
`database.postgis_db.get_cursor(commit=True)` or to manage the connection
explicitly. Idempotent on the natural keys (platform_name; chip_path).
"""

from __future__ import annotations

from typing import Iterable, Optional
import json


def upsert_reference_platform(
    cursor,
    *,
    platform_name: str,
    platform_family: str,
    ontology_object_id: Optional[str] = None,
    country_of_origin: Optional[str] = None,
    role: Optional[str] = None,
    attributes: Optional[dict] = None,
) -> str:
    """Upsert one platform by `platform_name` (UNIQUE). Returns the row id (UUID).

    Updates platform_family / role / attributes if the row already exists; does
    NOT touch centroids or view_domains (recompute_platform_centroids handles those).
    """
    cursor.execute(
        """
        INSERT INTO reference_platforms
            (platform_name, platform_family, ontology_object_id,
             country_of_origin, role, attributes)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (platform_name) DO UPDATE SET
            platform_family    = EXCLUDED.platform_family,
            ontology_object_id = EXCLUDED.ontology_object_id,
            country_of_origin  = EXCLUDED.country_of_origin,
            role               = EXCLUDED.role,
            attributes         = EXCLUDED.attributes,
            updated_at         = NOW()
        RETURNING id
        """,
        (
            platform_name,
            platform_family,
            ontology_object_id,
            country_of_origin,
            role,
            json.dumps(attributes or {}),
        ),
    )
    row = cursor.fetchone()
    return row["id"] if isinstance(row, dict) else row[0]


def insert_reference_chip(
    cursor,
    *,
    platform_id: str,
    view_domain: str,
    source_dataset: str,
    chip_path: str,
    embedding: Iterable[float],
    license_spdx: str,
    source_url: Optional[str] = None,
    attribution: Optional[str] = None,
    gsd_meters: Optional[float] = None,
    sensor: Optional[str] = None,
    bbox_in_source: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Insert one chip and its embedding. Idempotent on (platform_id, chip_path).

    `view_domain` is either 'overhead' (embedding lands in embedding_overhead)
    or 'ground' (embedding_ground). `embedding` must be length 1024 for
    overhead, 512 for ground.
    """
    if view_domain not in ("overhead", "ground"):
        raise ValueError(f"view_domain must be 'overhead' or 'ground', got {view_domain!r}")

    expected_dim = 1024 if view_domain == "overhead" else 512
    if len(embedding) != expected_dim:
        raise ValueError(
            f"{view_domain} embedding must be {expected_dim}-d; got {len(embedding)}"
        )

    # Preserve numpy.ndarray as-is so the pgvector adapter's ndarray
    # dispatch fires; `list(np.ndarray)` would produce a list of np.float32
    # scalars that psycopg2 cannot adapt. Plain iterables go through list().
    try:
        import numpy as _np  # local import keeps the helper numpy-optional
        _is_np = isinstance(embedding, _np.ndarray)
    except ImportError:
        _is_np = False
    emb = embedding if _is_np else list(embedding)
    overhead_col = emb if view_domain == "overhead" else None
    ground_col = emb if view_domain == "ground" else None

    cursor.execute(
        """
        INSERT INTO reference_chips
            (platform_id, view_domain, source_dataset, source_url, license_spdx,
             attribution, gsd_meters, sensor, chip_path, bbox_in_source, metadata,
             embedding_overhead, embedding_ground)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        ON CONFLICT (platform_id, chip_path) DO UPDATE SET
            source_dataset     = EXCLUDED.source_dataset,
            source_url         = EXCLUDED.source_url,
            license_spdx       = EXCLUDED.license_spdx,
            attribution        = EXCLUDED.attribution,
            gsd_meters         = EXCLUDED.gsd_meters,
            sensor             = EXCLUDED.sensor,
            bbox_in_source     = EXCLUDED.bbox_in_source,
            metadata           = EXCLUDED.metadata,
            embedding_overhead = EXCLUDED.embedding_overhead,
            embedding_ground   = EXCLUDED.embedding_ground
        RETURNING id
        """,
        (
            platform_id,
            view_domain,
            source_dataset,
            source_url,
            license_spdx,
            attribution,
            gsd_meters,
            sensor,
            chip_path,
            json.dumps(bbox_in_source) if bbox_in_source is not None else None,
            json.dumps(metadata or {}),
            overhead_col,
            ground_col,
        ),
    )
    row = cursor.fetchone()
    return row["id"] if isinstance(row, dict) else row[0]


def recompute_platform_centroids(cursor, *, platform_id: Optional[str] = None) -> int:
    """Recompute `reference_platforms.centroid_overhead` / `centroid_ground`
    as the per-domain mean of their chips' embeddings.

    Updates `view_domains` to reflect which centroids became non-null. Returns
    the number of platform rows updated.

    If `platform_id` is given, only that platform is recomputed; otherwise all
    platforms with at least one chip are recomputed.

    Note: this does NOT clear stale centroids on platforms that have lost all
    their chips — the CTE only emits rows for platform_ids that still have at
    least one chip. To retire a platform, DELETE the reference_platforms row
    (its chips CASCADE-delete via the FK).
    """
    where_clause = "AND p.id = %s" if platform_id else ""
    params = (platform_id,) if platform_id else ()
    cursor.execute(
        f"""
        WITH agg AS (
            SELECT
                c.platform_id,
                AVG(c.embedding_overhead) FILTER (WHERE c.view_domain = 'overhead' AND c.embedding_overhead IS NOT NULL) AS centroid_overhead,
                AVG(c.embedding_ground)   FILTER (WHERE c.view_domain = 'ground'   AND c.embedding_ground   IS NOT NULL) AS centroid_ground
            FROM reference_chips c
            GROUP BY c.platform_id
        )
        UPDATE reference_platforms p
           SET centroid_overhead = agg.centroid_overhead,
               centroid_ground   = agg.centroid_ground,
               view_domains      = (
                   CASE WHEN agg.centroid_overhead IS NOT NULL THEN ARRAY['overhead']::text[] ELSE '{{}}'::text[] END
                 ||CASE WHEN agg.centroid_ground   IS NOT NULL THEN ARRAY['ground']::text[]   ELSE '{{}}'::text[] END
               ),
               updated_at = NOW()
          FROM agg
         WHERE p.id = agg.platform_id
           {where_clause}
        """,
        params,
    )
    return cursor.rowcount


def find_similar_platforms(
    cursor,
    *,
    embedding: Iterable[float],
    view_domain: str = "overhead",
    top_k: int = 3,
    candidate_pool: int = 20,
    top_chips_per_platform: int = 3,
) -> list[dict]:
    """Return top-k platforms whose centroid is closest to the given embedding.

    Two-stage retrieval:
      1. Centroid HNSW search → top `candidate_pool` platforms (cheap, dense).
      2. Re-rank by best per-chip cosine score among each winner's chips (refined).

    Returns a list of dicts ordered by descending score:
        [{"platform_id": str, "platform_name": str, "platform_family": str,
          "score": float, "matched_chip_ids": list[str]}, ...]

    `score` is `1 - cosine_distance` so values are in approximately [-1, 1];
    for unit-normalised DINOv3-SAT vectors they land in [0, 1].

    Returns an empty list if no platform has a centroid in `view_domain`.
    Note: platforms whose centroid is present but who have ZERO chips in
    `view_domain` are silently skipped during the per-chip re-rank — they
    will not appear in the results even if their centroid scored high in
    Stage 1.
    """
    if view_domain not in ("overhead", "ground"):
        raise ValueError(f"view_domain must be 'overhead' or 'ground', got {view_domain!r}")

    # Render the query embedding as a pgvector text literal and cast every
    # distance term below with %s::vector. This is robust whether or not the
    # pgvector psycopg2 adapter registered on this connection — a plain Python
    # list would otherwise bind as numeric[], and `vector <=> numeric[]` raises
    # UndefinedFunction (every auto-identify failed this way during ingest).
    # See database.py:_VectorAwareConnection.
    try:
        import numpy as _np
        _seq = embedding.tolist() if isinstance(embedding, _np.ndarray) else list(embedding)
    except ImportError:
        _seq = list(embedding)
    q = "[" + ",".join(repr(float(x)) for x in _seq) + "]"

    centroid_col = "centroid_overhead" if view_domain == "overhead" else "centroid_ground"
    chip_col = "embedding_overhead" if view_domain == "overhead" else "embedding_ground"

    # Stage 1: centroid HNSW top-K
    cursor.execute(
        f"""
        SELECT id, platform_name, platform_family,
               1 - ({centroid_col} <=> %s::vector) AS centroid_score
          FROM reference_platforms
         WHERE {centroid_col} IS NOT NULL
         ORDER BY {centroid_col} <=> %s::vector
         LIMIT %s
        """,
        (q, q, candidate_pool),
    )
    centroid_winners = cursor.fetchall()
    if not centroid_winners:
        return []

    winner_ids = [(r["id"] if isinstance(r, dict) else r[0]) for r in centroid_winners]
    winner_names = {
        (r["id"] if isinstance(r, dict) else r[0]): {
            "platform_name": r["platform_name"] if isinstance(r, dict) else r[1],
            "platform_family": r["platform_family"] if isinstance(r, dict) else r[2],
        }
        for r in centroid_winners
    }

    # Stage 2: for each winner, find the best per-chip cosine. We do one
    # round-trip with a window function — gives best-chip-per-platform
    # and avoids N+1 SELECTs.
    cursor.execute(
        f"""
        WITH ranked AS (
            SELECT c.platform_id,
                   c.id::text AS chip_id,
                   1 - (c.{chip_col} <=> %s::vector) AS chip_score,
                   ROW_NUMBER() OVER (
                       PARTITION BY c.platform_id
                       ORDER BY c.{chip_col} <=> %s::vector
                   ) AS rn
              FROM reference_chips c
             WHERE c.platform_id = ANY(%s::uuid[])
               AND c.view_domain = %s
               AND c.{chip_col} IS NOT NULL
        )
        SELECT platform_id::text AS platform_id,
               MAX(chip_score) AS best_chip_score,
               array_agg(chip_id ORDER BY chip_score DESC) FILTER (WHERE rn <= %s) AS top_chip_ids
          FROM ranked
         GROUP BY platform_id
         ORDER BY best_chip_score DESC
         LIMIT %s
        """,
        (q, q, winner_ids, view_domain, top_chips_per_platform, top_k),
    )
    rows = cursor.fetchall()

    results = []
    for r in rows:
        pid = r["platform_id"] if isinstance(r, dict) else r[0]
        score = r["best_chip_score"] if isinstance(r, dict) else r[1]
        chip_ids = r["top_chip_ids"] if isinstance(r, dict) else r[2]
        info = winner_names.get(pid, {"platform_name": None, "platform_family": None})
        results.append({
            "platform_id": pid,
            "platform_name": info["platform_name"],
            "platform_family": info["platform_family"],
            "score": float(score) if score is not None else 0.0,
            "matched_chip_ids": list(chip_ids) if chip_ids else [],
        })
    return results


def _upsert_platform_identification(
    cursor,
    *,
    detection_id: int,
    platform_name: str,
    platform_family: Optional[str],
    platform_confidence: float,
    platform_source: str,
    updated_by: str,
) -> None:
    """Write the four platform_* columns to object_details for `detection_id`.

    Shared by both the auto path (`attach_identification_candidates`,
    `platform_source='auto'`, `updated_by='reference-db-auto-identify'`)
    and the analyst-approve path (Plan D router, `platform_source='analyst'`,
    `updated_by=<session-username>`).

    Touches ONLY the four platform_* columns + housekeeping; analyst-asserted
    columns (threat_level, affiliation, designation, notes, etc.) are
    preserved by ON CONFLICT DO UPDATE SET semantics — unlisted columns
    survive. Intentional contract — see
    docs/decisions/why-auto-write-with-threshold.md.
    """
    if platform_source not in ("auto", "analyst", "manual"):
        raise ValueError(
            f"platform_source must be 'auto'|'analyst'|'manual', got {platform_source!r}"
        )
    cursor.execute(
        """
        INSERT INTO object_details
            (source, source_id, platform_name, platform_family,
             platform_confidence, platform_source, updated_by)
        VALUES ('detection', %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_id) DO UPDATE SET
            platform_name        = EXCLUDED.platform_name,
            platform_family      = EXCLUDED.platform_family,
            platform_confidence  = EXCLUDED.platform_confidence,
            platform_source      = EXCLUDED.platform_source,
            updated_at           = NOW(),
            updated_by           = EXCLUDED.updated_by
        """,
        (
            str(detection_id),
            platform_name,
            platform_family,
            float(platform_confidence),
            platform_source,
            updated_by,
        ),
    )


def attach_identification_candidates(
    cursor,
    *,
    detection_id: int,
    embedding: Iterable[float],
    view_domain: str = "overhead",
    auto_threshold: float = 0.85,
    top_k: int = 3,
) -> int:
    """For a freshly-inserted detection, compute top-k reference-platform
    candidates and persist them.

    Behaviour:
      - Calls `find_similar_platforms` to get top-k candidates.
      - Deletes any existing `platform_identification_candidates` rows for
        this `detection_id` (so a re-run replaces, not duplicates).
      - Inserts one `platform_identification_candidates` row per candidate.
      - If top-1 score >= `auto_threshold`, marks that row `auto_applied` and
        writes `platform_name` / `platform_family` / `platform_confidence`
        / `platform_source='auto'` to `object_details` via a direct UPSERT
        (mirrors detection_helpers.py's column shape).
      - Below threshold, all rows land as `pending` and `object_details` is
        left untouched.

    Returns the number of candidate rows written. Returns 0 if no candidates
    were found (e.g. reference DB empty for this view_domain), in which case
    `object_details` is also not modified.
    """
    candidates = find_similar_platforms(
        cursor,
        embedding=embedding,
        view_domain=view_domain,
        top_k=top_k,
    )

    # Idempotency: replace any prior candidates for this detection.
    # DELETE first so an empty-candidates re-run still clears stale rows.
    cursor.execute(
        "DELETE FROM platform_identification_candidates WHERE detection_id = %s",
        (detection_id,),
    )
    if not candidates:
        return 0

    top_score = candidates[0]["score"]
    auto_applied = top_score >= auto_threshold

    for rank, cand in enumerate(candidates, start=1):
        is_top = (rank == 1)
        status = "auto_applied" if (is_top and auto_applied) else "pending"
        applied_at_sql = "NOW()" if status == "auto_applied" else "NULL"
        cursor.execute(
            f"""
            INSERT INTO platform_identification_candidates
                (detection_id, platform_id, score, rank, matched_chip_ids,
                 status, applied_at)
            VALUES (%s, %s, %s, %s, %s::uuid[], %s, {applied_at_sql})
            """,
            (
                detection_id,
                cand["platform_id"],
                cand["score"],
                rank,
                cand["matched_chip_ids"] or [],
                status,
            ),
        )

    # Auto-apply to object_details only when top-1 cleared the threshold.
    # See _upsert_platform_identification for the conflict-policy contract.
    if auto_applied:
        top = candidates[0]
        _upsert_platform_identification(
            cursor,
            detection_id=detection_id,
            platform_name=top["platform_name"],
            platform_family=top["platform_family"],
            platform_confidence=float(top["score"]),
            platform_source="auto",
            updated_by="reference-db-auto-identify",
        )

    return len(candidates)
