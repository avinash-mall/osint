"""Phase 2-6 graph projector / builder Celery tasks (NEAR, colocation, GNN,
entity proposal/resimilarity, repeat detector, ontology/observation/doc projectors)."""

from worker.config import *  # noqa: F401,F403
from worker.app import celery_app  # noqa: F401

_NEAR_RADIUS_M = {
    "base": 5000.0,
    "launchpoint": 2000.0,
    "launch_point": 2000.0,
    "facility": 1000.0,
}


def _near_radius_for_kind(kind: str) -> float | None:
    """Phase 5.B: prefer admin-configured threshold; fall back to env default.

    Lazy-imports the threshold helper to avoid a worker → routers cycle.
    """
    try:
        from routers.admin_thresholds import get_current_threshold
        row = get_current_threshold(kind)
        if row and row.get("near_radius_m"):
            return float(row["near_radius_m"])
    except Exception:
        logger.debug("near_radius_for_kind: threshold lookup failed for %s", kind, exc_info=True)
    return _NEAR_RADIUS_M.get(kind.lower())


@celery_app.task(name="worker.tick_near_builder", queue="default")
def tick_near_builder() -> dict:
    """Phase 4 beat task: MERGE ``:NEAR`` edges from Detections to Base/
    LaunchPoint/Facility sites.

    For each AOI tagged with an ``aoi_kind``, find Detections inserted since
    the last successful run for that site (cursor in ``near_builder_state``)
    whose centroid falls within the per-class radius, then MERGE the NEAR
    edge with ``distance_m``. Incremental — re-running is cheap.
    """
    from graph_writes import project_near_edges_batch

    total_edges = 0
    sites_processed = 0
    sites_skipped = 0
    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, metadata FROM aois
                WHERE metadata ? 'aoi_kind'
                  AND metadata->>'aoi_kind' IN ('base', 'launchpoint', 'launch_point', 'facility')
                """
            )
            sites = [dict(r) for r in cursor.fetchall()]
    except Exception as exc:
        logger.exception("tick_near_builder: AOI fetch failed")
        return {"error": "aoi_fetch_failed", "detail": str(exc)}

    for site in sites:
        aoi_id = int(site["id"])
        meta = site.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        kind = (meta.get("aoi_kind") or "").lower()
        # Phase 5.B: thresholds may be admin-edited per site kind.
        radius_m = _near_radius_for_kind(kind)
        if not radius_m:
            sites_skipped += 1
            continue
        site_id = f"aoi-{aoi_id}"

        try:
            with postgis_db.get_cursor(commit=True) as cursor:
                cursor.execute(
                    "INSERT INTO near_builder_state (site_id, last_detection_id) VALUES (%s, 0) ON CONFLICT (site_id) DO NOTHING",
                    (site_id,),
                )
                cursor.execute("SELECT last_detection_id FROM near_builder_state WHERE site_id = %s", (site_id,))
                last_id_row = cursor.fetchone()
                last_id = int(last_id_row["last_detection_id"]) if last_id_row else 0
                cursor.execute(
                    """
                    WITH aoi AS (SELECT ST_Centroid(geom) AS centroid FROM aois WHERE id = %s)
                    SELECT d.id AS detection_postgis_id,
                           ST_Distance(d.centroid::geography, aoi.centroid::geography)::float AS distance_m
                    FROM detections d, aoi
                    WHERE d.deleted_at IS NULL
                      AND d.id > %s
                      AND ST_DWithin(d.centroid::geography, aoi.centroid::geography, %s)
                    ORDER BY d.id
                    LIMIT 5000
                    """,
                    (aoi_id, last_id, radius_m),
                )
                pairs = [dict(r) for r in cursor.fetchall()]
        except Exception:
            logger.exception("tick_near_builder: ST_DWithin failed for aoi=%s", aoi_id)
            sites_skipped += 1
            continue

        if pairs:
            rows = [
                {"detection_postgis_id": p["detection_postgis_id"], "site_id": site_id, "distance_m": p["distance_m"]}
                for p in pairs
            ]
            total_edges += project_near_edges_batch(rows)
            new_last = max(p["detection_postgis_id"] for p in pairs)
            try:
                with postgis_db.get_cursor(commit=True) as cursor:
                    cursor.execute(
                        "UPDATE near_builder_state SET last_detection_id = %s, last_run_at = NOW() WHERE site_id = %s",
                        (new_last, site_id),
                    )
            except Exception:
                logger.warning("tick_near_builder: cursor update failed for %s", site_id)
        else:
            # Touch last_run_at so the cursor table reflects activity.
            try:
                with postgis_db.get_cursor(commit=True) as cursor:
                    cursor.execute("UPDATE near_builder_state SET last_run_at = NOW() WHERE site_id = %s", (site_id,))
            except Exception:
                pass
        sites_processed += 1

    return {
        "sites_processed": sites_processed,
        "sites_skipped": sites_skipped,
        "near_edges_written": total_edges,
    }


@celery_app.task(name="worker.tick_colocation_builder", queue="default")
def tick_colocation_builder() -> dict:
    """Phase 6 beat task: MERGE ``COLOCATED_WITH`` proximity edges between
    detections that a spatial proximity graph links.

    Builds a kNN (default) or fixed-radius graph over the most-recent detection
    centroids within ``COLOCATION_WINDOW_DAYS`` and writes one
    ``COLOCATED_WITH`` edge per linked pair. MERGE makes re-running idempotent,
    so — unlike the NEAR builder — no per-site cursor is needed. The proximity
    maths lives in :mod:`graph_proximity` (vendored from city2graph); this task
    only does the PostGIS read + Neo4j write. See
    docs/decisions/why-proximity-colocation-graph.md.
    """
    from graph_proximity import build_colocation_edges
    from graph_writes import project_colocation_edges_batch

    window_days = env_int("COLOCATION_WINDOW_DAYS", 30)
    max_nodes = env_int("COLOCATION_MAX_NODES", 2000)
    method = (os.getenv("COLOCATION_METHOD") or "knn").strip().lower()
    k = env_int("COLOCATION_KNN_K", 6)
    radius_m = float(os.getenv("COLOCATION_RADIUS_M") or 3000.0)

    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, ST_X(centroid) AS lon, ST_Y(centroid) AS lat
                FROM detections
                WHERE deleted_at IS NULL
                  AND centroid IS NOT NULL
                  AND created_at >= NOW() - (%s || ' days')::interval
                ORDER BY id DESC
                LIMIT %s
                """,
                (str(window_days), max_nodes),
            )
            records = [(int(r["id"]), float(r["lon"]), float(r["lat"])) for r in cursor.fetchall()]
    except Exception as exc:
        logger.exception("tick_colocation_builder: detection fetch failed")
        return {"error": "detection_fetch_failed", "detail": str(exc)}

    if len(records) < 2:
        return {"nodes": len(records), "edges_written": 0, "method": method}

    try:
        rows = build_colocation_edges(records, method=method, k=k, radius_m=radius_m)
    except ValueError as exc:
        return {"error": "bad_method", "detail": str(exc)}

    written = 0
    for i in range(0, len(rows), 5000):
        written += project_colocation_edges_batch(rows[i:i + 5000])
    return {"nodes": len(records), "edges_written": written, "method": method}


@celery_app.task(name="worker.tick_gnn_link_prediction", queue="default")
def tick_gnn_link_prediction() -> dict:
    """Phase 6 beat task: GraphSAGE link prediction over the entity graph.

    Trains a GNN on the observed (non-candidate) edges and MERGEs the top
    predicted operational-entity links as advisory ``GNN_SUGGESTED_LINK`` edges
    for analyst review. Optional infrastructure: torch is not in the backend
    image by default, so this skips cleanly (mirroring the DEM/OSRM optionality)
    until torch is installed. See docs/decisions/why-gnn-link-prediction.md.
    """
    from graph_pyg import GNNUnavailable, is_torch_available, suggest_links
    from graph_writes import project_gnn_suggested_links_batch

    if not is_torch_available():
        return {"skipped": "torch_unavailable"}

    top_k = env_int("GNN_LINK_TOP_K", 50)
    limit = env_int("GNN_SNAPSHOT_LIMIT", 1500)
    feature_keys = ["confidence", "latitude", "longitude"]
    operational_labels = {"Target", "Asset", "Base", "LaunchPoint", "Facility", "Unit", "Vessel", "Aircraft", "Vehicle"}

    nodes: list[dict] = []
    seen: set[str] = set()
    operational: list[str] = []
    edges: list[tuple[str, str]] = []
    adjacency: set[tuple[str, str]] = set()
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (n)
                OPTIONAL MATCH (n)-[r]->(m)
                WHERE r IS NULL OR NOT type(r) STARTS WITH 'CANDIDATE_'
                RETURN n, r, m
                LIMIT $limit
                """,
                {"limit": limit},
            )
            for record in result:
                n = record["n"]
                m = record["m"]
                r = record["r"]
                for node in (n, m):
                    if node is None or node.element_id in seen:
                        continue
                    seen.add(node.element_id)
                    props = dict(node)
                    label = list(node.labels)[0] if node.labels else "Node"
                    nodes.append({"id": node.element_id, **{k: props.get(k) for k in feature_keys}})
                    if label in operational_labels:
                        operational.append(node.element_id)
                if r is not None and m is not None:
                    edges.append((n.element_id, m.element_id))
                    adjacency.add((n.element_id, m.element_id))
                    adjacency.add((m.element_id, n.element_id))
    except Exception as exc:
        logger.exception("tick_gnn_link_prediction: snapshot failed")
        return {"error": "snapshot_failed", "detail": str(exc)}

    candidate_pairs: list[tuple[str, str]] = []
    for i in range(len(operational)):
        for j in range(i + 1, len(operational)):
            a, b = operational[i], operational[j]
            if (a, b) not in adjacency:
                candidate_pairs.append((a, b))

    try:
        suggestions = suggest_links(nodes, edges, candidate_pairs, feature_keys, top_k=top_k)
    except GNNUnavailable as exc:
        return {"skipped": str(exc)}

    written = project_gnn_suggested_links_batch(suggestions)
    return {"nodes": len(nodes), "candidates": len(candidate_pairs), "suggested": len(suggestions), "edges_written": written}


_CLASS_TO_ENTITY_KIND = {
    "vessel": "vessel", "ship": "vessel", "boat": "vessel", "container_ship": "vessel",
    "vehicle": "vehicle", "truck": "vehicle", "car": "vehicle", "tank": "vehicle",
    "aircraft": "aircraft", "helicopter": "aircraft", "fighter": "aircraft", "plane": "aircraft",
}


def _fetch_repeated_at_clusters() -> list[dict]:
    """Read REPEATED_AT cluster rows (used by both proposer variants)."""
    try:
        with db.get_session() as session:
            return [dict(r) for r in session.run(
                """
                MATCH (d:Detection)-[r:REPEATED_AT]->(s)
                WHERE any(l IN labels(s) WHERE l IN ['Base', 'LaunchPoint', 'Facility'])
                RETURN r.detection_class AS detection_class,
                       r.count AS cluster_count,
                       s.id AS site_id,
                       s.name AS site_name,
                       d.postgis_id AS sample_detection_id
                LIMIT 200
                """
            )]
    except Exception:
        logger.exception("tick_propose_entities: REPEATED_AT walk failed")
        return []


def _llm_propose_entities(clusters: list[dict]) -> list[dict]:
    """Phase 5.I: ask the LLM to propose operational entities from clusters.

    Returns a list of ``{entity_kind, proposed_name, reason, seed_detection_ids,
    proposed_metadata}`` dicts. Raises ``AIUnavailable`` on transport failure
    or unparseable response so the caller can fall through to the heuristic.

    Hard-caps the prompt at 60 cluster rows so the request stays well inside
    the token budget of typical local LLMs (gemma, llama variants).
    """
    import ai
    cluster_payload = []
    for c in clusters[:60]:
        cluster_payload.append({
            "detection_class": c.get("detection_class"),
            "cluster_count": int(c.get("cluster_count") or 0),
            "site_id": c.get("site_id"),
            "site_name": c.get("site_name"),
            "sample_detection_id": (
                int(c["sample_detection_id"]) if c.get("sample_detection_id") is not None else None
            ),
        })

    system_prompt = (
        "You propose operational entities (vessel / aircraft / vehicle / "
        "facility / unit) from REPEATED_AT detection clusters. "
        "Return ONLY a JSON object with key 'proposals' whose value is a list. "
        "Each proposal: {entity_kind, proposed_name, reason, seed_detection_ids:[int]}. "
        "entity_kind MUST be one of: vessel, aircraft, vehicle, facility, unit. "
        "Be conservative — propose only when the detection_class clearly implies "
        "a real operational entity. Skip if ambiguous."
    )
    user_prompt = f"clusters = {cluster_payload}"

    parsed = ai.get_llm_json(prompt=user_prompt, system=system_prompt, max_tokens=1200)
    raw_proposals = parsed.get("proposals") if isinstance(parsed, dict) else None
    if not isinstance(raw_proposals, list):
        return []

    valid_kinds = {"vessel", "aircraft", "vehicle", "facility", "unit"}
    out: list[dict] = []
    for p in raw_proposals:
        if not isinstance(p, dict):
            continue
        kind = str(p.get("entity_kind") or "").lower().strip()
        name = str(p.get("proposed_name") or "").strip()
        if kind not in valid_kinds or not name:
            continue
        seeds = p.get("seed_detection_ids") or []
        if not isinstance(seeds, list):
            seeds = []
        out.append({
            "entity_kind": kind,
            "proposed_name": name,
            "reason": str(p.get("reason") or "")[:500],
            "seed_detection_ids": [int(x) for x in seeds if isinstance(x, (int, str)) and str(x).isdigit()],
            "proposed_metadata": {"source": "llm"},
        })
    return out


def _heuristic_propose_entities(clusters: list[dict]) -> list[dict]:
    """Phase 5.I: original heuristic path extracted from tick_propose_entities."""
    out: list[dict] = []
    for row in clusters:
        cls = (row["detection_class"] or "").lower()
        kind = next((v for k, v in _CLASS_TO_ENTITY_KIND.items() if k in cls), None)
        if not kind:
            continue
        site_name = row.get("site_name") or row.get("site_id") or "site"
        out.append({
            "entity_kind": kind,
            "proposed_name": f"{cls} at {site_name}",
            "reason": f"{row['cluster_count']} {cls} detections clustered at {site_name}",
            "seed_detection_ids": [int(row["sample_detection_id"])] if row.get("sample_detection_id") is not None else [],
            "proposed_metadata": {"site_id": row.get("site_id"), "detection_class": cls, "source": "heuristic"},
            "score": min(1.0, 0.5 + (int(row.get("cluster_count") or 0) - 5) * 0.05),
        })
    return out


@celery_app.task(name="worker.tick_propose_entities", queue="default")
def tick_propose_entities() -> dict:
    """Phase 5.I: LLM-first proposer with heuristic fallback.

    Tries the LLM (via the OpenAI-compatible client in backend/ai.py, reading
    OPENAI_API_BASE / OPENAI_API_KEY / OPENAI_MODEL from .env). On
    AIUnavailable (LLM endpoint empty, unreachable, or returns unparseable
    JSON), falls back to the original REPEATED_AT-cluster heuristic so the
    pipeline keeps working air-gapped without LLM.

    Either way: skip proposals whose name+kind already exists as an
    operational entity or pending candidate.
    """
    import json as _json
    proposed = 0
    skipped = 0
    repeats = _fetch_repeated_at_clusters()
    if not repeats:
        return {"proposed": 0, "skipped": 0, "note": "no REPEATED_AT clusters to seed from"}

    proposals: list[dict] = []
    source = "heuristic"
    try:
        from ai import AIUnavailable
        llm_proposals = _llm_propose_entities(repeats)
        if llm_proposals:
            proposals = llm_proposals
            source = "llm"
    except Exception as exc:
        # Catches AIUnavailable too — fall back to heuristic.
        logger.info("tick_propose_entities: LLM path unavailable (%s); using heuristic", exc)
    if not proposals:
        proposals = _heuristic_propose_entities(repeats)

    with postgis_db.get_cursor(commit=True) as cursor:
        for p in proposals:
            kind = p["entity_kind"]
            proposed_name = p["proposed_name"]
            # SAVEPOINT per proposal: catching a SQL error without one poisons
            # the shared transaction, silently rolling back every earlier
            # insert while the task still reports them written.
            cursor.execute("SAVEPOINT proposal")
            try:
                cursor.execute(
                    "SELECT 1 FROM operational_entities WHERE name = %s AND kind = %s LIMIT 1",
                    (proposed_name, kind),
                )
                if cursor.fetchone():
                    skipped += 1
                    cursor.execute("RELEASE SAVEPOINT proposal")
                    continue
                cursor.execute(
                    "SELECT 1 FROM entity_candidates WHERE proposed_name = %s AND entity_kind = %s AND status = 'pending' LIMIT 1",
                    (proposed_name, kind),
                )
                if cursor.fetchone():
                    skipped += 1
                    cursor.execute("RELEASE SAVEPOINT proposal")
                    continue
                cursor.execute(
                    """
                    INSERT INTO entity_candidates (entity_kind, proposed_name, seed_detection_ids, score, reason, proposed_metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        kind, proposed_name,
                        p.get("seed_detection_ids") or [],
                        float(p.get("score", 0.6)),
                        p.get("reason", ""),
                        _json.dumps(p.get("proposed_metadata") or {}),
                    ),
                )
                proposed += 1
                cursor.execute("RELEASE SAVEPOINT proposal")
            except Exception:
                cursor.execute("ROLLBACK TO SAVEPOINT proposal")
                logger.exception("tick_propose_entities: insert failed for %s", proposed_name)
                skipped += 1

    return {"proposed": proposed, "skipped": skipped, "source": source}


def _parse_embedding_anchor(blob: Any) -> list[float] | None:
    """Detection_tracks.embedding_anchor is either a raw float array or
    a {"fp16_b64": ..., "dim": ...} packed struct. Return a plain list[float]
    or None when the shape is unrecognised.
    """
    if blob is None:
        return None
    if isinstance(blob, str):
        try:
            blob = json.loads(blob)
        except json.JSONDecodeError:
            return None
    if isinstance(blob, list):
        try:
            return [float(x) for x in blob]
        except (TypeError, ValueError):
            return None
    if isinstance(blob, dict) and "fp16_b64" in blob and "dim" in blob:
        try:
            import base64 as _b64
            import numpy as _np
            raw = _b64.b64decode(blob["fp16_b64"])
            arr = _np.frombuffer(raw, dtype=_np.float16).astype(_np.float32)
            return arr.tolist()
        except Exception:
            return None
    return None


@celery_app.task(name="worker.tick_aggregate_entity_embeddings", queue="default")
def tick_aggregate_entity_embeddings() -> dict:
    """Phase 5.J: average ``detection_tracks.embedding_anchor`` per operational
    entity (using the ``operational_entity_tracks`` association table) and
    store the centroid in ``operational_entities.re_id_embedding``.

    Entities with no attached tracks leave re_id_embedding NULL — the cosine
    branch of tick_entity_resimilarity skips them gracefully and falls back
    to the name-match heuristic.
    """
    aggregated = 0
    skipped = 0
    with postgis_db.get_cursor() as cursor:
        cursor.execute("SELECT id FROM operational_entities")
        entity_ids = [r["id"] for r in cursor.fetchall()]

    for entity_id in entity_ids:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT t.embedding_anchor
                FROM operational_entity_tracks et
                JOIN detection_tracks t ON t.id = et.track_id
                WHERE et.entity_id = %s AND t.embedding_anchor IS NOT NULL
                """,
                (entity_id,),
            )
            anchors = [r["embedding_anchor"] for r in cursor.fetchall()]

        vectors = [v for v in (_parse_embedding_anchor(a) for a in anchors) if v]
        if not vectors:
            skipped += 1
            continue
        dim = len(vectors[0])
        if not all(len(v) == dim for v in vectors):
            skipped += 1
            continue
        centroid = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]

        try:
            with postgis_db.get_cursor(commit=True) as cursor:
                cursor.execute(
                    """
                    UPDATE operational_entities
                    SET re_id_embedding = %s::jsonb,
                        re_id_dim = %s,
                        re_id_updated_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (json.dumps(centroid), dim, entity_id),
                )
            aggregated += 1
        except Exception:
            logger.exception("tick_aggregate_entity_embeddings: update failed for %s", entity_id)
            skipped += 1

    return {"aggregated": aggregated, "skipped": skipped, "total": len(entity_ids)}


@celery_app.task(name="worker.tick_entity_resimilarity", queue="default")
def tick_entity_resimilarity() -> dict:
    """Phase 4 beat task: emit ``:POSSIBLY_SAME_AS`` candidate edges between
    operational entities that might refer to the same real-world thing.

    Phase 4 ships a cheap offline heuristic (case-insensitive name-prefix
    overlap within the same ``kind``); the DINOv3-embedding similarity path
    is the upgrade slot — when the embedding service is wired, swap the
    `_proposals_for_kind` body for an O(N²) cosine pass over re-id
    embeddings without changing the task's interface.

    All candidates are bounded by ``ENTITY_RESIMILARITY_MAX_PAIRS`` per run
    (default 500) so this never floods the analyst review queue.
    """
    from graph_writes import merge_possibly_same_as_batch

    max_pairs = env_int("ENTITY_RESIMILARITY_MAX_PAIRS", 500)
    rows: list[dict] = []

    def _proposals_for_kind(kind: str) -> list[dict]:
        meta = scope_meta_cache.setdefault(kind, _load_entity_meta(kind))
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name FROM operational_entities
                WHERE kind = %s
                ORDER BY id
                """,
                (kind,),
            )
            entities = [(r["id"], (r["name"] or "").strip().lower()) for r in cursor.fetchall()]
        out: list[dict] = []
        for i, (a_id, a_name) in enumerate(entities):
            if not a_name:
                continue
            for b_id, b_name in entities[i + 1:]:
                if not b_name or a_id == b_id:
                    continue
                # Phase 5.K: skip pairs that fail the time / AOI scope.
                if not _pair_passes_scope(a_id, b_id, meta):
                    continue
                # Cheap score: shared 4+ char prefix OR substring containment.
                shared = 0
                while shared < min(len(a_name), len(b_name)) and a_name[shared] == b_name[shared]:
                    shared += 1
                contained = a_name in b_name or b_name in a_name
                score = 0.0
                if contained:
                    score = max(score, 0.8)
                if shared >= 4:
                    score = max(score, 0.5 + 0.05 * (shared - 4))
                if score >= 0.5:
                    out.append({"a_id": a_id, "b_id": b_id, "score": round(min(score, 0.99), 3), "source": "name_match"})
                    if len(out) >= max_pairs:
                        return out
        return out


    # Phase 5.K: time + AOI scoping for both branches.
    window_days = env_int("ENTITY_RESIMILARITY_WINDOW_DAYS", 30)
    aoi_scoped = os.getenv("ENTITY_RESIMILARITY_AOI_SCOPED", "true").lower() not in ("0", "false", "no")

    def _load_entity_meta(kind: str) -> dict[str, dict]:
        """Per-entity metadata for scoping: last-activity timestamp + AOI."""
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, operates_from_base_id, COALESCE(updated_at, created_at) AS last_activity
                FROM operational_entities WHERE kind = %s
                """,
                (kind,),
            )
            return {r["id"]: dict(r) for r in cursor.fetchall()}

    def _pair_passes_scope(a_id: str, b_id: str, meta: dict[str, dict]) -> bool:
        a_meta = meta.get(a_id); b_meta = meta.get(b_id)
        if not a_meta or not b_meta:
            return True  # missing metadata — don't block
        # AOI scope: both entities have an operates_from_base_id and they differ → skip
        if aoi_scoped:
            a_aoi = a_meta.get("operates_from_base_id")
            b_aoi = b_meta.get("operates_from_base_id")
            if a_aoi and b_aoi and a_aoi != b_aoi:
                return False
        # Time scope: both have last_activity and they're >window_days apart → skip
        a_t = a_meta.get("last_activity"); b_t = b_meta.get("last_activity")
        if a_t and b_t:
            try:
                from datetime import timedelta
                if abs((a_t - b_t).days) > window_days:
                    return False
            except Exception:
                pass
        return True

    def _embedding_proposals_for_kind(kind: str) -> list[dict]:
        """Phase 5.J + 5.K: cosine over re_id_embedding, scoped by AOI + time."""
        from graph_writes import cosine_similarity as _cos
        threshold = env_float("ENTITY_RESIMILARITY_EMBEDDING_THRESHOLD", 0.85)
        meta = _load_entity_meta(kind)
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, re_id_embedding, re_id_dim FROM operational_entities
                WHERE kind = %s AND re_id_embedding IS NOT NULL
                ORDER BY id
                """,
                (kind,),
            )
            entities = []
            for r in cursor.fetchall():
                emb = r["re_id_embedding"]
                if isinstance(emb, str):
                    try:
                        emb = json.loads(emb)
                    except json.JSONDecodeError:
                        continue
                if not isinstance(emb, list):
                    continue
                entities.append((r["id"], emb))
        out: list[dict] = []
        for i, (a_id, a_vec) in enumerate(entities):
            for b_id, b_vec in entities[i + 1:]:
                if not _pair_passes_scope(a_id, b_id, meta):
                    continue
                cos = _cos(a_vec, b_vec)
                if cos is None or cos < threshold:
                    continue
                out.append({
                    "a_id": a_id, "b_id": b_id,
                    "score": round(float(cos), 3),
                    "source": "embedding",
                })
                if len(out) >= max_pairs:
                    return out
        return out

    seen_pairs: set[tuple[str, str]] = set()
    scope_meta_cache: dict[str, dict[str, dict]] = {}
    for kind in ("vessel", "aircraft", "vehicle", "facility", "unit"):
        # Phase 5.J: prefer embedding cosine; fall back to name-match
        # heuristic so entities without embeddings still get proposals.
        try:
            emb_rows = _embedding_proposals_for_kind(kind)
            for r in emb_rows:
                key = tuple(sorted([r["a_id"], r["b_id"]]))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                rows.append(r)
        except Exception:
            logger.exception("tick_entity_resimilarity: kind=%s embedding branch failed", kind)
        try:
            heuristic_rows = _proposals_for_kind(kind)
            for r in heuristic_rows:
                key = tuple(sorted([r["a_id"], r["b_id"]]))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                rows.append(r)
        except Exception:
            logger.exception("tick_entity_resimilarity: kind=%s heuristic branch failed", kind)
        if len(rows) >= max_pairs:
            rows = rows[:max_pairs]
            break

    written = merge_possibly_same_as_batch(rows)
    return {
        "proposals": len(rows),
        "edges_written": written,
        "max_pairs": max_pairs,
        "by_source": {s: sum(1 for r in rows if r.get("source") == s) for s in {r.get("source", "unknown") for r in rows}},
    }


@celery_app.task(name="worker.tick_repeat_detector", queue="default")
def tick_repeat_detector() -> dict:
    """Phase 4 beat task: MERGE ``:REPEATED_AT`` edges.

    For each Base/LaunchPoint/Facility node, walk its :NEAR-connected
    Detections grouped by class, and when the class count over the last
    ``REPEAT_DETECTOR_WINDOW_DAYS`` (default 30) days is ≥
    ``REPEAT_DETECTOR_MIN_COUNT`` (default 5), MERGE a representative
    ``:REPEATED_AT`` edge from the most-recent member detection. Built on
    top of the NEAR edges laid down by ``worker.tick_near_builder``.
    """
    from graph_writes import project_repeated_at_batch

    # Phase 5.B: admin-configured threshold per kind overrides env defaults
    # when present. The worker scans all three kinds at once, so we read the
    # max window + min min_count across the configured rows so analyst-
    # tightened settings flow through without per-kind beat dispatch.
    env_window_days = env_int("REPEAT_DETECTOR_WINDOW_DAYS", 30)
    env_min_count = env_int("REPEAT_DETECTOR_MIN_COUNT", 5)
    window_days = env_window_days
    min_count = env_min_count
    try:
        from routers.admin_thresholds import get_current_threshold
        configured = [get_current_threshold(k) for k in ("base", "launchpoint", "facility")]
        configured = [r for r in configured if r]
        if configured:
            # Use the most permissive admin-set window (analyst wants to see
            # longer-running patterns) and the strictest min_count (analyst
            # wants to suppress noise).
            window_days = max(int(r["window_days"]) for r in configured) or env_window_days
            min_count = max(int(r["min_count"]) for r in configured) or env_min_count
    except Exception:
        logger.debug("tick_repeat_detector: threshold lookup failed", exc_info=True)
    rows_to_write: list[dict] = []
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (s)<-[:NEAR]-(d:Detection)
                WHERE any(l IN labels(s) WHERE l IN ['Base', 'LaunchPoint', 'Facility'])
                  AND d.created_at IS NOT NULL
                  AND d.created_at >= datetime() - duration({days: $window_days})
                WITH s, d.class AS cls, collect(d) AS dets
                WHERE size(dets) >= $min_count
                WITH s, cls, size(dets) AS cnt,
                     reduce(latest = head(dets), d IN dets |
                            CASE WHEN d.created_at > latest.created_at THEN d ELSE latest END) AS sample
                RETURN s.id AS site_id, cls AS detection_class,
                       sample.postgis_id AS sample_detection_id, cnt AS count
                """,
                {"window_days": window_days, "min_count": min_count},
            )
            for r in result:
                if r["site_id"] is None or r["sample_detection_id"] is None:
                    continue
                rows_to_write.append({
                    "site_id": r["site_id"],
                    "detection_class": r["detection_class"],
                    "sample_detection_id": int(r["sample_detection_id"]),
                    "count": int(r["count"]),
                    "window_days": window_days,
                    "radius_m": None,
                })
    except Exception as exc:
        logger.exception("tick_repeat_detector: NEAR walk failed")
        return {"error": "near_walk_failed", "detail": str(exc)}

    written = project_repeated_at_batch(rows_to_write)
    return {
        "candidates_evaluated": len(rows_to_write),
        "edges_written": written,
        "window_days": window_days,
        "min_count": min_count,
    }


@celery_app.task(name="worker.project_label_of_edges", queue="default")
def project_label_of_edges(detection_ids: list[int] | None = None, batch_size: int = 500) -> dict:
    """MERGE ``(d:Detection)-[:LABEL_OF]->(o:OntologyObject)`` for Detections
    whose ``class`` normalizes to a known ontology object.

    Two call styles:
    - ``project_label_of_edges(detection_ids=[1,2,3])`` — targeted refresh
      (e.g. after a satellite pass writes new detections).
    - ``project_label_of_edges()`` — backfill mode: walks every Detection in
      PostGIS, batched, calling ``ontology.normalize`` to resolve each class
      to an OntologyObject id.
    """
    from graph_writes import project_label_of_for_detection_class
    import ontology as ontology_module

    def _select(ids: list[int] | None, offset: int) -> list[dict]:
        with postgis_db.get_cursor() as cursor:
            if ids:
                cursor.execute(
                    "SELECT id, class FROM detections WHERE id = ANY(%s) AND deleted_at IS NULL",
                    (ids,),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, class FROM detections
                    WHERE deleted_at IS NULL
                    ORDER BY id OFFSET %s LIMIT %s
                    """,
                    (offset, batch_size),
                )
            return [dict(r) for r in cursor.fetchall()]

    def _project(rows: list[dict]) -> int:
        # Group detection ids by normalized OntologyObject id.
        by_object: dict[str, list[int]] = {}
        by_object_class: dict[str, str] = {}
        for row in rows:
            try:
                norm = ontology_module.normalize(row["class"])
            except Exception:
                continue
            object_id = getattr(norm, "ontology_object_id", None) if norm else None
            if not object_id:
                continue
            by_object.setdefault(object_id, []).append(int(row["id"]))
            by_object_class.setdefault(object_id, str(row["class"]))
        total = 0
        for object_id, det_ids in by_object.items():
            total += project_label_of_for_detection_class(
                detection_class=by_object_class[object_id],
                ontology_object_id=object_id,
                detection_postgis_ids=det_ids,
            )
        return total

    if detection_ids is not None:
        rows = _select(detection_ids, 0)
        return {"projected": _project(rows), "mode": "targeted", "count": len(detection_ids)}

    total = 0
    offset = 0
    while True:
        try:
            rows = _select(None, offset)
        except Exception:
            logger.exception("project_label_of_edges: batch read failed at offset=%s", offset)
            break
        if not rows:
            break
        total += _project(rows)
        if len(rows) < batch_size:
            break
        offset += batch_size
    return {"projected": total, "mode": "backfill"}


@celery_app.task(name="worker.project_ontology_to_graph", queue="default")
def project_ontology_to_graph() -> dict:
    """Mirror every ``ontology_branches`` + ``ontology_objects`` row into Neo4j.

    Triggered on ``ontology.bump_version`` so the graph stays in sync with the
    PostGIS canonical taxonomy. Idempotent (MERGE on row id). Returns counts.
    """
    from graph_writes import project_ontology_branches_and_objects
    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, parent_id, label, color, short, icon_key, order_index
                FROM ontology_branches
                ORDER BY order_index ASC, id ASC
                """
            )
            branches = [dict(r) for r in cursor.fetchall()]
            cursor.execute(
                """
                SELECT id, branch_id, label, prompt, icon_key, order_index
                FROM ontology_objects
                ORDER BY order_index ASC, id ASC
                """
            )
            objects = [dict(r) for r in cursor.fetchall()]
    except Exception as exc:
        logger.exception("project_ontology_to_graph: PostGIS read failed")
        return {"error": "postgis_read_failed", "detail": str(exc)}
    return project_ontology_branches_and_objects(branches=branches, objects=objects)


@celery_app.task(name="worker.project_unknown_labels", queue="default")
def project_unknown_labels(label: str | None = None, supports_limit: int = 5) -> dict:
    """Mirror ``ontology_unknown_labels`` rows into Neo4j ``:UnknownLabel`` nodes.

    Two call styles:
    - ``project_unknown_labels(label="something")`` — single-label refresh
      (used by the on-write hook in ``ontology._log_unknown``).
    - ``project_unknown_labels()`` — backfill: walks every row, projects each.

    ``supports_limit`` caps how many recent detections per label are wired up
    via ``:LABEL_OF`` so the Ontology mode orbit doesn't grow unbounded.
    """
    from graph_writes import project_unknown_label

    def _fetch(where_label: str | None) -> list[dict]:
        with postgis_db.get_cursor() as cursor:
            if where_label is not None:
                cursor.execute(
                    """
                    SELECT label, layer, count, first_seen::text AS first_seen,
                           last_seen::text AS last_seen, suggested_branch_id
                    FROM ontology_unknown_labels WHERE label = %s
                    """,
                    (where_label,),
                )
            else:
                cursor.execute(
                    """
                    SELECT label, layer, count, first_seen::text AS first_seen,
                           last_seen::text AS last_seen, suggested_branch_id
                    FROM ontology_unknown_labels
                    ORDER BY count DESC NULLS LAST, label
                    LIMIT 1000
                    """,
                )
            return [dict(r) for r in cursor.fetchall()]

    def _recent_supports(label_value: str) -> list[int]:
        try:
            with postgis_db.get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id FROM detections
                    WHERE class = %s AND deleted_at IS NULL
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (label_value, supports_limit),
                )
                return [int(r["id"]) for r in cursor.fetchall()]
        except Exception:
            return []

    rows = _fetch(label)
    if not rows:
        return {"projected": 0, "mode": "single" if label else "backfill"}
    projected = 0
    for row in rows:
        ok = project_unknown_label(
            label=row["label"],
            layer=row.get("layer"),
            count=int(row.get("count") or 0),
            first_seen=row.get("first_seen"),
            last_seen=row.get("last_seen"),
            suggested_branch_id=row.get("suggested_branch_id"),
            supporting_detection_ids=_recent_supports(row["label"]),
        )
        if ok:
            projected += 1
    return {"projected": projected, "mode": "single" if label else "backfill", "total_seen": len(rows)}


@celery_app.task(name="worker.project_observations_to_graph", queue="default")
def project_observations_to_graph(observation_id: int | None = None, batch_size: int = 200) -> dict:
    """Mirror ``observations`` rows with ``entity_id`` into Neo4j.

    Two call styles:
    - ``project_observations_to_graph(observation_id=42)`` — project the
      single row (used by the on-insert hook in ``events.record_observation``).
    - ``project_observations_to_graph()`` — backfill mode: walks every
      observation whose ``entity_id`` is set and whose ``postgis_id`` is not
      yet present in Neo4j, in batches of ``batch_size``.
    """
    from graph_writes import project_observation_batch

    def _select_single() -> list[dict]:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id AS postgis_id, entity_id, event_type, title, confidence,
                       observed_at::text AS observed_at,
                       ST_Y(geom) AS latitude, ST_X(geom) AS longitude
                FROM observations
                WHERE id = %s AND entity_id IS NOT NULL
                """,
                (observation_id,),
            )
            return [dict(r) for r in cursor.fetchall()]

    def _select_batch(offset: int) -> list[dict]:
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
            return [dict(r) for r in cursor.fetchall()]

    if observation_id is not None:
        rows = _select_single()
        if not rows:
            return {"projected": 0, "mode": "single", "observation_id": observation_id}
        return {"projected": project_observation_batch(rows), "mode": "single", "observation_id": observation_id}

    total = 0
    offset = 0
    while True:
        try:
            rows = _select_batch(offset)
        except Exception:
            logger.exception("project_observations_to_graph: batch read failed at offset=%s", offset)
            break
        if not rows:
            break
        total += project_observation_batch(rows)
        if len(rows) < batch_size:
            break
        offset += batch_size
    return {"projected": total, "mode": "backfill"}


@celery_app.task(name="worker.project_documents_to_graph", queue="default")
def project_documents_to_graph(document_id: int) -> dict:
    """Mirror a ``documents`` row into Neo4j as a ``:Document`` stub.

    Called after extraction populates ``extracted_entities``. Resolves each
    entity to existing Target/Asset/Vessel/Aircraft/Vehicle/Unit nodes by
    cheap case-insensitive substring match on ``n.name`` and writes
    ``(:Document)-[:MENTIONS {confidence, source_label}]->(:Operational)``
    edges. The PostGIS row keeps the full extraction; the graph only carries
    the resolution.
    """
    from graph_writes import load_entity_label_index, project_document_with_mentions
    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, title, media_type, summary, extracted_entities
                FROM documents WHERE id = %s
                """,
                (document_id,),
            )
            row = cursor.fetchone()
    except Exception as exc:
        logger.exception("project_documents_to_graph: PostGIS read failed for doc %s", document_id)
        return {"document_id": document_id, "error": "postgis_read_failed", "detail": str(exc)}
    if not row:
        return {"document_id": document_id, "error": "document_not_found"}
    doc = dict(row)
    extracted = doc.get("extracted_entities") or []
    if isinstance(extracted, str):
        try:
            extracted = json.loads(extracted)
        except json.JSONDecodeError:
            extracted = []
    # Phase 5.A: skip projection entirely when the LLM extracted no entities —
    # a :Document stub with zero MENTIONS adds graph noise without buying any
    # analyst-visible link. The PostGIS row remains the source of truth and a
    # later re-extraction can trigger the projector again.
    if not extracted:
        return {"document_id": doc["id"], "skipped": "no_extracted_entities"}
    index = load_entity_label_index()
    counts = project_document_with_mentions(
        document_id=doc["id"],
        title=doc.get("title") or f"doc-{doc['id']}",
        media_type=doc.get("media_type"),
        summary=doc.get("summary"),
        extracted_entities=extracted,
        entity_label_index=index,
    )
    return {"document_id": doc["id"], **counts}


@celery_app.task(name="worker.project_fmv_to_graph", queue="default")
def project_fmv_to_graph(clip_id: int) -> dict:
    """Mirror an FMV clip + consolidated tracks into Neo4j (Phase 2 projector).

    Reads the ``fmv_clips`` row and aggregates ``fmv_detections`` by the
    consolidated ``metadata.track_id``, then MERGE-projects a ``:FMVClip``
    stub + one ``:FMVDetection`` per track linked via ``CONTAINS_DETECTION``.

    Source of truth stays in PostGIS (per
    [decisions/why-postgis-and-neo4j-coexist.md](../docs/decisions/why-postgis-and-neo4j-coexist.md));
    the Neo4j nodes carry only identity + a few headline properties so
    Evidence-mode column DAGs can chase the chain without re-fetching
    frame-level data.
    """
    from graph_writes import project_fmv_clip_and_tracks
    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, duration_seconds, fps, width, height
                FROM fmv_clips WHERE id = %s
                """,
                (clip_id,),
            )
            clip_row = cursor.fetchone()
            if not clip_row:
                return {"clip_id": clip_id, "error": "clip_not_found"}
            clip = dict(clip_row)

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
                ORDER BY (metadata->>'track_id'), class
                """,
                (clip_id,),
            )
            track_rows = [dict(r) for r in cursor.fetchall()]
    except Exception as exc:
        logger.exception("project_fmv_to_graph: PostGIS read failed for clip %s", clip_id)
        return {"clip_id": clip_id, "error": "postgis_read_failed", "detail": str(exc)}

    # If multiple class-votes survive per track_uid (rare under consolidation),
    # keep the highest-confidence row per track to one FMVDetection node.
    by_uid: dict[str, dict[str, Any]] = {}
    for row in track_rows:
        uid = row.get("track_uid")
        if not uid:
            continue
        existing = by_uid.get(uid)
        if existing is None or (row.get("confidence") or 0) > (existing.get("confidence") or 0):
            by_uid[uid] = row
    tracks = list(by_uid.values())

    counts = project_fmv_clip_and_tracks(
        clip_id=clip_id,
        clip_name=clip.get("name") or f"clip-{clip_id}",
        duration_seconds=clip.get("duration_seconds"),
        fps=clip.get("fps"),
        width=clip.get("width"),
        height=clip.get("height"),
        tracks=tracks,
    )
    return {"clip_id": clip_id, **counts}




__all__ = [n for n in dir() if not n.startswith("__")]
