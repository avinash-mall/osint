"""Neo4j schema bootstrap — idempotent constraints + indexes for the Link Graph.

Called once from the FastAPI lifespan in ``main.py``. Mirrors the
``platform_schema.ensure_platform_tables`` pattern: a process-level lock guards
against concurrent first-run setup, and every statement uses ``IF NOT EXISTS``
so the function is safe to re-invoke.

See [docs/architecture/link-graph-redesign.md](../docs/architecture/link-graph-redesign.md)
for the rationale behind each label / property.
"""

from __future__ import annotations

import logging
import threading

from database import db

logger = logging.getLogger(__name__)

_graph_schema_lock = threading.Lock()
_graph_schema_ready = False


# (label, property_or_tuple) — single property uses str; composite uses tuple.
_NODE_CONSTRAINTS: list[tuple[str, str | tuple[str, ...]]] = [
    ("Target", "id"),
    ("Detection", "postgis_id"),
    ("SatellitePass", "postgis_id"),
    ("FMVClip", "postgis_id"),
    ("FMVDetection", ("clip_id", "track_uid")),
    ("Document", "postgis_id"),
    ("Report", "postgis_id"),
    ("FeedEvent", "postgis_id"),
    ("Observation", "postgis_id"),
    ("Asset", "id"),
    ("Base", "id"),
    ("LaunchPoint", "id"),
    ("Facility", "id"),
    ("Unit", "id"),
    ("OntologyBranch", "id"),
    ("OntologyObject", "id"),
    ("OntologyCandidate", "key"),
    ("UnknownLabel", "label"),
]


def _constraint_statement(label: str, prop: str | tuple[str, ...]) -> tuple[str, str]:
    """Return ``(constraint_name, cypher)`` for a uniqueness constraint."""
    if isinstance(prop, tuple):
        props = ", ".join(f"n.{p}" for p in prop)
        name = f"uniq_{label.lower()}_" + "_".join(prop)
        return name, (
            f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE ({props}) IS UNIQUE"
        )
    name = f"uniq_{label.lower()}_{prop}"
    return name, f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"


_NODE_INDEXES: list[tuple[str, str, str]] = [
    # (name, label, comma-separated properties)
    ("idx_detection_class_created", "Detection", "d.class, d.created_at"),
]


def ensure_graph_schema() -> None:
    """Create Neo4j uniqueness constraints and indexes used by the Link Graph.

    No-op after the first successful run within a process. Safe to call from
    the FastAPI lifespan and from one-off scripts. Errors are logged but never
    raised — the API must still come up even if Neo4j is briefly unreachable
    at startup; the caller can retry on demand.
    """

    global _graph_schema_ready
    if _graph_schema_ready:
        return
    with _graph_schema_lock:
        if _graph_schema_ready:
            return
        try:
            with db.get_session() as session:
                for label, prop in _NODE_CONSTRAINTS:
                    name, cypher = _constraint_statement(label, prop)
                    try:
                        session.run(cypher)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("graph_schema: constraint %s failed: %s", name, exc)

                for index_name, label, expr in _NODE_INDEXES:
                    try:
                        session.run(
                            f"CREATE INDEX {index_name} IF NOT EXISTS FOR (d:{label}) ON ({expr})"
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("graph_schema: index %s failed: %s", index_name, exc)

                # Relationship-property index for NEAR (Phase 4 will populate;
                # the index is harmless to create now and saves a migration later).
                try:
                    session.run(
                        "CREATE INDEX idx_near_distance IF NOT EXISTS FOR ()-[r:NEAR]->() ON (r.distance_m)"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("graph_schema: NEAR distance index failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("graph_schema: bootstrap skipped (%s); will retry on next call", exc)
            return
        _graph_schema_ready = True
        logger.info("graph_schema: %d constraints + indexes ensured", len(_NODE_CONSTRAINTS))


def reset_cache_for_tests() -> None:
    """Allow tests to re-run ``ensure_graph_schema`` against a fresh Neo4j."""
    global _graph_schema_ready
    with _graph_schema_lock:
        _graph_schema_ready = False
