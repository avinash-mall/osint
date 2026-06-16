"""The singleton Celery app + beat schedule + prefork DB-pool reset handler."""

from worker.config import *  # noqa: F401,F403

celery_app = Celery("sentinel_worker", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.beat_schedule = {
    "tick-collection-scheduler": {
        "task": "worker.tick_collection_scheduler",
        "schedule": float(env_int("COLLECTION_SCHEDULER_INTERVAL_S", 300)),
    },
    "tick-feed-poll": {
        "task": "worker.tick_feed_poll",
        "schedule": float(env_int("FEED_POLL_INTERVAL_S", 60)),
    },
    # Hourly retention sweep over feed-driven observations + timeline_events
    # so the two append-only tables don't grow unbounded.
    "cleanup-old-observations": {
        "task": "worker.cleanup_old_observations",
        "schedule": float(env_int("OBSERVATION_CLEANUP_INTERVAL_S", 60 * 60)),
    },
    # Phase 4.C: build :NEAR edges from Detections to Base/LaunchPoint/Facility.
    "tick-near-builder": {
        "task": "worker.tick_near_builder",
        "schedule": float(env_int("NEAR_BUILDER_INTERVAL_S", 60 * 60)),
    },
    # Phase 6: MERGE COLOCATED_WITH proximity edges between recent detections
    # (kNN / fixed-radius graph from graph_proximity, vendored from city2graph).
    "tick-colocation-builder": {
        "task": "worker.tick_colocation_builder",
        "schedule": float(env_int("COLOCATION_BUILDER_INTERVAL_S", 6 * 60 * 60)),
    },
    # Phase 4.D: detect classes that repeat at a site and write REPEATED_AT.
    "tick-repeat-detector": {
        "task": "worker.tick_repeat_detector",
        "schedule": float(env_int("REPEAT_DETECTOR_INTERVAL_S", 24 * 60 * 60)),
    },
    # Phase 6: GraphSAGE link prediction → advisory GNN_SUGGESTED_LINK edges.
    # No-ops cleanly until torch is installed in the image (optional infra).
    "tick-gnn-link-prediction": {
        "task": "worker.tick_gnn_link_prediction",
        "schedule": float(env_int("GNN_LINK_PREDICTION_INTERVAL_S", 24 * 60 * 60)),
    },
    # Phase 4.E: weekly name-match/embedding similarity → POSSIBLY_SAME_AS.
    "tick-entity-resimilarity": {
        "task": "worker.tick_entity_resimilarity",
        "schedule": float(env_int("ENTITY_RESIMILARITY_INTERVAL_S", 7 * 24 * 60 * 60)),
    },
    # Phase 4.F: daily heuristic proposal of new operational entities from
    # REPEATED_AT clusters that lack matching operational_entities rows.
    "tick-propose-entities": {
        "task": "worker.tick_propose_entities",
        "schedule": float(env_int("ENTITY_PROPOSAL_INTERVAL_S", 24 * 60 * 60)),
    },
    # Phase 5.J: twice-daily aggregation of detection_tracks.embedding_anchor
    # into operational_entities.re_id_embedding so tick_entity_resimilarity
    # can use cosine similarity.
    "tick-aggregate-entity-embeddings": {
        "task": "worker.tick_aggregate_entity_embeddings",
        "schedule": float(env_int("ENTITY_EMBEDDING_AGGREGATION_INTERVAL_S", 12 * 60 * 60)),
    },
}
celery_app.conf.timezone = "UTC"


from celery.signals import worker_process_init


@worker_process_init.connect
def _reset_db_pool_after_fork(**_kwargs):
    """Rebuild the PostGIS pool in every prefork child.

    Importing this module can issue a DB query in the Celery
    MainProcess, which builds ``postgis_db``'s connection pool *before* the
    prefork pool forks its workers. libpq connections are not fork-safe, so
    every child must discard the inherited pool and lazily build its own —
    otherwise the first task fails with ``DatabaseError: error with status
    PGRES_TUPLES_OK and no message from the libpq``.

    See docs/decisions/reset-db-pool-after-fork.md.
    """
    postgis_db.reset_after_fork()


__all__ = [n for n in dir() if not n.startswith("__")]
