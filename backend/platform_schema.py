"""Idempotent ``CREATE TABLE IF NOT EXISTS`` migrations for the platform schema.

Called from FastAPI startup and from a handful of request paths that need to
guarantee their target tables exist before issuing CRUD. The advisory lock
(``acquire_schema_xact_lock``) serializes concurrent migrations across worker
processes.

State is module-scoped so ``ensure_platform_tables`` is a no-op after the first
successful call within a process.
"""

from __future__ import annotations

import logging
import threading

from database import postgis_db

logger = logging.getLogger(__name__)

_platform_schema_lock = threading.Lock()
_platform_schema_ready = False


def acquire_schema_xact_lock(cursor, lock_name: str = "sentinel_platform_schema") -> None:
    """Take a transaction-scoped advisory lock so concurrent ``ensure_*``
    callers don't race on ``CREATE TABLE IF NOT EXISTS``."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_name,))


def ensure_feed_tables() -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        acquire_schema_xact_lock(cursor)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feed_sources (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                feed_type VARCHAR(100) NOT NULL,
                protocol VARCHAR(50) NOT NULL,
                endpoint VARCHAR(1024) NOT NULL,
                topic VARCHAR(255) DEFAULT 'feeds',
                parser VARCHAR(100),
                enabled BOOLEAN DEFAULT TRUE,
                status VARCHAR(50) DEFAULT 'configured',
                last_error TEXT,
                last_seen TIMESTAMP WITH TIME ZONE,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feed_events (
                id SERIAL PRIMARY KEY,
                source_id INTEGER REFERENCES feed_sources(id) ON DELETE CASCADE,
                event_type VARCHAR(100),
                payload JSONB DEFAULT '{}',
                geom GEOMETRY(POINT, 4326),
                observed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        cursor.execute("ALTER TABLE feed_sources ADD COLUMN IF NOT EXISTS poll_interval_seconds INTEGER DEFAULT 60")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_sources_type ON feed_sources(feed_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_events_geom ON feed_events USING GIST(geom)")


def ensure_collection_tables() -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        acquire_schema_xact_lock(cursor)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS collection_tasks (
                id SERIAL PRIMARY KEY,
                target_id VARCHAR(255) NOT NULL,
                target_name VARCHAR(255),
                asset_type VARCHAR(100) DEFAULT 'ISR',
                priority VARCHAR(50),
                queue VARCHAR(100),
                status VARCHAR(50) DEFAULT 'proposed',
                notes TEXT,
                aipoints JSONB DEFAULT '[]',
                requested_by VARCHAR(100) DEFAULT 'ui',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        cursor.execute("ALTER TABLE collection_tasks ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMP WITH TIME ZONE")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_tasks_target ON collection_tasks(target_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_tasks_status ON collection_tasks(status)")


def ensure_platform_tables() -> None:
    """Run every platform migration once per process. Cached via a module flag."""
    global _platform_schema_ready
    if _platform_schema_ready:
        return

    with _platform_schema_lock:
        if _platform_schema_ready:
            return

        ensure_feed_tables()
        ensure_collection_tables()
        with postgis_db.get_cursor(commit=True) as cursor:
            acquire_schema_xact_lock(cursor)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS upload_jobs (
                    id SERIAL PRIMARY KEY,
                    upload_id VARCHAR(64) UNIQUE NOT NULL,
                    filename VARCHAR(255) NOT NULL,
                    file_path VARCHAR(1024) NOT NULL,
                    media_type VARCHAR(80) NOT NULL,
                    handler VARCHAR(120),
                    status VARCHAR(50) DEFAULT 'stored',
                    celery_task_id VARCHAR(255),
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'")
            cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64)")
            cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS source_filename VARCHAR(255)")
            cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_passes_source_time ON satellite_passes(source_hash, acquisition_time)")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vector_layers (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    file_path VARCHAR(1024) NOT NULL,
                    layer_type VARCHAR(80) DEFAULT 'vector',
                    feature_count INTEGER DEFAULT 0,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fmv_clips (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    file_path VARCHAR(1024) NOT NULL,
                    hls_path VARCHAR(1024),
                    duration_seconds REAL DEFAULT 0,
                    width INTEGER,
                    height INTEGER,
                    fps REAL,
                    status VARCHAR(50) DEFAULT 'stored',
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fmv_frames (
                    id SERIAL PRIMARY KEY,
                    clip_id INTEGER REFERENCES fmv_clips(id) ON DELETE CASCADE,
                    frame_index INTEGER NOT NULL,
                    timestamp_seconds REAL NOT NULL,
                    telemetry JSONB DEFAULT '{}',
                    footprint GEOMETRY(POLYGON, 4326),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    UNIQUE (clip_id, frame_index)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fmv_detections (
                    id SERIAL PRIMARY KEY,
                    clip_id INTEGER REFERENCES fmv_clips(id) ON DELETE CASCADE,
                    frame_index INTEGER NOT NULL,
                    class VARCHAR(100) NOT NULL,
                    confidence REAL DEFAULT 0,
                    bbox JSONB DEFAULT '[]',
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id SERIAL PRIMARY KEY,
                    track_uid VARCHAR(255) UNIQUE NOT NULL,
                    source_id INTEGER REFERENCES feed_sources(id) ON DELETE SET NULL,
                    label VARCHAR(100) DEFAULT 'Track',
                    callsign VARCHAR(255),
                    latest_payload JSONB DEFAULT '{}',
                    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS track_points (
                    id SERIAL PRIMARY KEY,
                    track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
                    geom GEOMETRY(POINT, 4326),
                    speed REAL,
                    heading REAL,
                    payload JSONB DEFAULT '{}',
                    observed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS aois (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    priority VARCHAR(50) DEFAULT 'Medium',
                    geom GEOMETRY(POLYGON, 4326),
                    metadata JSONB DEFAULT '{}',
                    -- Phase 6.26: per-AOI default allegiance for incoming
                    -- detections. "unknown" preserves current behaviour;
                    -- analysts working a known-hostile theatre can flip
                    -- this to "hostile" so new detections start with the
                    -- right colour/threat default instead of always going
                    -- through the neutral assumption.
                    default_allegiance VARCHAR(20) DEFAULT 'unknown',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            # Existing installs predate the default_allegiance column; backfill it.
            cursor.execute("""
                ALTER TABLE aois
                ADD COLUMN IF NOT EXISTS default_allegiance VARCHAR(20) DEFAULT 'unknown'
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS collection_requirements (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    priority VARCHAR(50) DEFAULT 'Medium',
                    status VARCHAR(50) DEFAULT 'draft',
                    target_id VARCHAR(255),
                    aoi JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ped_tasks (
                    id SERIAL PRIMARY KEY,
                    requirement_id INTEGER REFERENCES collection_requirements(id) ON DELETE SET NULL,
                    collection_task_id INTEGER REFERENCES collection_tasks(id) ON DELETE SET NULL,
                    title VARCHAR(255) NOT NULL,
                    status VARCHAR(50) DEFAULT 'queued',
                    assignee VARCHAR(100),
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analytics_jobs (
                    id SERIAL PRIMARY KEY,
                    job_type VARCHAR(100) NOT NULL,
                    status VARCHAR(50) DEFAULT 'complete',
                    input JSONB DEFAULT '{}',
                    result JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    target_id VARCHAR(255),
                    report_type VARCHAR(80) DEFAULT 'target_package',
                    status VARCHAR(50) DEFAULT 'ready',
                    content JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS training_jobs (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    dataset_path VARCHAR(1024),
                    epochs INTEGER DEFAULT 1,
                    status VARCHAR(50) DEFAULT 'queued',
                    metrics JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS models (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    version VARCHAR(80) DEFAULT 'local',
                    model_path VARCHAR(1024),
                    status VARCHAR(50) DEFAULT 'available',
                    metrics JSONB DEFAULT '{}',
                    promoted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id SERIAL PRIMARY KEY,
                    domain VARCHAR(50) NOT NULL,
                    source_id INTEGER REFERENCES feed_sources(id) ON DELETE SET NULL,
                    entity_id VARCHAR(255),
                    event_type VARCHAR(120) DEFAULT 'observation',
                    title VARCHAR(255),
                    confidence REAL DEFAULT 0,
                    geom GEOMETRY(POINT, 4326),
                    payload JSONB DEFAULT '{}',
                    provenance JSONB DEFAULT '{}',
                    observed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    ingested_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS timeline_events (
                    id SERIAL PRIMARY KEY,
                    domain VARCHAR(50) NOT NULL,
                    event_type VARCHAR(120) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    source_id INTEGER REFERENCES feed_sources(id) ON DELETE SET NULL,
                    entity_id VARCHAR(255),
                    payload JSONB DEFAULT '{}',
                    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    upload_id VARCHAR(64),
                    domain VARCHAR(50) DEFAULT 'OSINT',
                    title VARCHAR(255) NOT NULL,
                    file_path VARCHAR(1024),
                    source_url VARCHAR(2048),
                    media_type VARCHAR(80) DEFAULT 'document',
                    status VARCHAR(50) DEFAULT 'stored',
                    summary TEXT,
                    extracted_entities JSONB DEFAULT '[]',
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transcripts (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    language VARCHAR(32) DEFAULT 'unknown',
                    text TEXT,
                    confidence REAL DEFAULT 0,
                    segments JSONB DEFAULT '[]',
                    status VARCHAR(50) DEFAULT 'placeholder',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_action_proposals (
                    id SERIAL PRIMARY KEY,
                    action_type VARCHAR(120) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    domain VARCHAR(50),
                    target_id VARCHAR(255),
                    rationale TEXT,
                    sources JSONB DEFAULT '[]',
                    payload JSONB DEFAULT '{}',
                    -- Phase 2.9: confidence is NOT NULL with no default so a
                    -- caller that forgets to score a proposal fails loudly
                    -- instead of inheriting an arbitrary 0.55 optimism prior.
                    confidence REAL NOT NULL,
                    risk_level VARCHAR(50) DEFAULT 'low',
                    status VARCHAR(50) DEFAULT 'pending_approval',
                    proposed_by VARCHAR(100) DEFAULT 'llm',
                    approved_by VARCHAR(100),
                    executed_at TIMESTAMP WITH TIME ZONE,
                    result JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS detection_target_candidates (
                    id SERIAL PRIMARY KEY,
                    detection_id INTEGER REFERENCES detections(id) ON DELETE CASCADE,
                    target_id VARCHAR(255) NOT NULL,
                    target_name VARCHAR(255),
                    score REAL DEFAULT 0,
                    reason TEXT,
                    status VARCHAR(50) DEFAULT 'pending',
                    evidence JSONB DEFAULT '{}',
                    reviewed_by VARCHAR(100),
                    reviewed_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    UNIQUE (detection_id, target_id)
                )
            """)
            # Phase 4: operational entities (Vessel/Aircraft/Vehicle/Facility/Unit).
            # PostGIS row is canonical; Neo4j carries a mirror node keyed by `id`.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operational_entities (
                    id VARCHAR(255) PRIMARY KEY,
                    kind VARCHAR(40) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    callsign VARCHAR(120),
                    hull VARCHAR(120),
                    entity_class VARCHAR(120),
                    unit_id VARCHAR(255),
                    operates_from_base_id VARCHAR(255),
                    metadata JSONB DEFAULT '{}',
                    created_by VARCHAR(100),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    CHECK (kind IN ('vessel', 'aircraft', 'vehicle', 'facility', 'unit', 'asset'))
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_operational_entities_kind ON operational_entities(kind)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_operational_entities_unit_id ON operational_entities(unit_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_operational_entities_operates_from ON operational_entities(operates_from_base_id)")
            # Phase 4: LLM/heuristic-proposed operational entities awaiting review.
            # Mirrors the detection_target_candidates pattern; analyst approves
            # via /api/operational-entity-candidates/{id}/approve.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS entity_candidates (
                    id SERIAL PRIMARY KEY,
                    entity_kind VARCHAR(40) NOT NULL,
                    proposed_name VARCHAR(255) NOT NULL,
                    seed_detection_ids INTEGER[] DEFAULT '{}',
                    score REAL DEFAULT 0,
                    reason TEXT,
                    status VARCHAR(50) DEFAULT 'pending',
                    proposed_metadata JSONB DEFAULT '{}',
                    reviewed_by VARCHAR(100),
                    reviewed_at TIMESTAMP WITH TIME ZONE,
                    approved_entity_id VARCHAR(255),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_candidates_kind_status ON entity_candidates(entity_kind, status)")
            # Phase 4: tracks the last incremental `worker.tick_near_builder`
            # cursor per static-feature id, so we only re-evaluate new Detections.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS near_builder_state (
                    site_id VARCHAR(255) PRIMARY KEY,
                    last_detection_id INTEGER DEFAULT 0,
                    last_run_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ontology_updates (
                    id SERIAL PRIMARY KEY,
                    source_type VARCHAR(80) NOT NULL,
                    source_id VARCHAR(255),
                    domain VARCHAR(50) DEFAULT 'OSINT',
                    status VARCHAR(50) DEFAULT 'pending_review',
                    summary TEXT,
                    proposed_entities JSONB DEFAULT '[]',
                    proposed_relationships JSONB DEFAULT '[]',
                    context JSONB DEFAULT '{}',
                    error TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS datasets (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    dataset_type VARCHAR(80) DEFAULT 'object_detection',
                    domain VARCHAR(50) DEFAULT 'GEOINT',
                    file_path VARCHAR(1024),
                    status VARCHAR(50) DEFAULT 'stored',
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fmv_frames_clip ON fmv_frames(clip_id, frame_index)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_track_points_geom ON track_points USING GIST(geom)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_aois_geom ON aois USING GIST(geom)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_domain_time ON observations(domain, observed_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_geom ON observations USING GIST(geom)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_timeline_domain_time ON timeline_events(domain, occurred_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_domain ON documents(domain)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_action_status ON ai_action_proposals(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_detection_target_candidates_detection ON detection_target_candidates(detection_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_detection_target_candidates_target ON detection_target_candidates(target_id, status)")
            # Phase 6.25: configurable threat policy. Rules are matched by
            # (class, category, allegiance); the most-specific match wins
            # (class > category > allegiance-only). When no rule matches,
            # threat stays "unrated" (the open-vocab default).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS threat_rules (
                    id SERIAL PRIMARY KEY,
                    -- Match keys; NULL = wildcard (matches anything).
                    class VARCHAR(255),
                    category VARCHAR(80),
                    allegiance VARCHAR(20),
                    -- Outcome.
                    threat_level VARCHAR(20) NOT NULL CHECK (threat_level IN ('low', 'medium', 'high', 'critical', 'unrated')),
                    threat_confidence REAL NOT NULL DEFAULT 0.8,
                    rationale TEXT,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_threat_rules_class ON threat_rules(class) WHERE enabled = TRUE")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_threat_rules_category ON threat_rules(category) WHERE enabled = TRUE")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ontology_updates_source ON ontology_updates(source_type, source_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ontology_updates_status ON ontology_updates(status)")

            # --- Auth + object-details + soft-delete schema --------------------
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auth_config (
                    id          INTEGER PRIMARY KEY DEFAULT 1,
                    config      JSONB   NOT NULL DEFAULT '{}'::jsonb,
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by  TEXT,
                    CHECK (id = 1)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS object_details (
                    id                       BIGSERIAL PRIMARY KEY,
                    source                   TEXT NOT NULL,
                    source_id                TEXT NOT NULL,
                    designation              TEXT,
                    object_class             TEXT,
                    military_classification  TEXT,
                    threat_level             TEXT,
                    affiliation              TEXT,
                    confidence_override      REAL,
                    notes                    TEXT,
                    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by               TEXT,
                    UNIQUE (source, source_id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_object_details_source ON object_details(source, source_id)")
            cursor.execute("ALTER TABLE detections     ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
            cursor.execute("ALTER TABLE detections     ADD COLUMN IF NOT EXISTS source     TEXT DEFAULT 'ai'")
            cursor.execute("ALTER TABLE detections     ADD COLUMN IF NOT EXISTS threat_level TEXT")
            cursor.execute("ALTER TABLE detections     ADD COLUMN IF NOT EXISTS affiliation  TEXT")
            cursor.execute("ALTER TABLE fmv_detections ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
            cursor.execute("ALTER TABLE fmv_detections ADD COLUMN IF NOT EXISTS threat_level TEXT")
            cursor.execute("ALTER TABLE fmv_detections ADD COLUMN IF NOT EXISTS affiliation  TEXT")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_detections_deleted_at     ON detections(deleted_at) WHERE deleted_at IS NULL")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fmv_detections_deleted_at ON fmv_detections(deleted_at) WHERE deleted_at IS NULL")
            cursor.execute("ALTER TABLE transcripts ALTER COLUMN status SET DEFAULT 'pending'")
            cursor.execute("UPDATE transcripts SET status = 'pending' WHERE status = 'placeholder'")

            # --- Round 2: DB-backed inference config, prompt profiles, version history ---
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inference_config (
                    id          INTEGER PRIMARY KEY DEFAULT 1,
                    config      JSONB   NOT NULL DEFAULT '{}'::jsonb,
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by  TEXT,
                    CHECK (id = 1)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prompt_profiles (
                    id           BIGSERIAL PRIMARY KEY,
                    sensor       TEXT NOT NULL,
                    name         TEXT NOT NULL,
                    version      TEXT NOT NULL,
                    prompts      JSONB NOT NULL DEFAULT '[]'::jsonb,
                    current      BOOLEAN NOT NULL DEFAULT FALSE,
                    notes        TEXT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_by   TEXT,
                    UNIQUE (sensor, version)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prompt_profiles_sensor_current ON prompt_profiles(sensor, current)")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ontology_version_history (
                    id                   BIGSERIAL PRIMARY KEY,
                    version_id           BIGINT NOT NULL,
                    summary              TEXT,
                    changes              JSONB NOT NULL DEFAULT '{}'::jsonb,
                    detections_at_cut    BIGINT,
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_by           TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_onto_history_version ON ontology_version_history(version_id DESC)")

        _platform_schema_ready = True


def auto_seed_ontology_if_empty() -> None:
    """If the ontology tables exist but contain only the bootstrap 'Other'
    row, populate them from ``backend/scripts/seeds/defenceOntology.seed.json``.
    Idempotent — no-op when objects already exist."""
    try:
        with postgis_db.get_cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM ontology_objects")
            row = cur.fetchone()
            n_objects = int(row["n"] if isinstance(row, dict) else row[0])
        if n_objects > 0:
            logger.info("ontology auto-seed: %d objects present, skipping seed", n_objects)
            return
        logger.warning("ontology auto-seed: DB has 0 objects — seeding from JSON")
        from scripts.seed_ontology import seed as _seed
        n_branches, n_objects_seeded, branch_writes, object_writes = _seed(reseed=False)
        logger.info(
            "ontology auto-seed complete: branches=%d objects=%d (writes=%d/%d)",
            n_branches, n_objects_seeded, branch_writes, object_writes,
        )
    except Exception as exc:
        # Don't crash the app — log loudly so an operator can re-run the
        # seed manually via `python -m backend.scripts.seed_ontology`.
        logger.exception("ontology auto-seed failed: %s", exc)
