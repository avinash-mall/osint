import os
import logging
import threading
from neo4j import GraphDatabase
import psycopg2
from psycopg2 import pool
from psycopg2.extensions import connection as Psycopg2Connection
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class _VectorAwareConnection(Psycopg2Connection):
    """psycopg2 connection that registers pgvector's vector adapter on first use.

    Wired into every pool/connect call via ``connection_factory`` so any
    Python writer (list/numpy/Vector) round-trips into a ``vector(N)`` column
    without each callsite remembering to call ``register_vector(conn)``.

    Registration is lazy (deferred until the first ``cursor()`` call) because
    the adapter requires the ``vector`` extension to exist in the target DB,
    which is only guaranteed after ``ensure_reference_platform_tables()`` has
    run. A connection handed out before the extension exists is still usable
    for everything except pgvector — we silently skip registration and retry
    on the next ``cursor()`` so subsequent callers see the adapter once the
    extension is installed.
    """

    _vector_registered = False

    def cursor(self, *args, **kwargs):
        if not self._vector_registered:
            # Set the flag FIRST to break the re-entrancy cycle: register_vector
            # itself calls conn.cursor(...) internally to query pg_type, which
            # dispatches back to this override. Reset on failure so a later call
            # retries once the extension is available.
            self._vector_registered = True
            try:
                from pgvector.psycopg2 import register_vector
                register_vector(self)
            except (ImportError, psycopg2.Error) as e:
                self._vector_registered = False
                logger.debug("pgvector adapter registration deferred: %s", e)
        return super().cursor(*args, **kwargs)

# Neo4j Configuration.
# Production credentials come from the environment: docker-compose injects
# NEO4J_PASSWORD (fail-closed `${NEO4J_PASSWORD:?...}`) from .env. The literal
# fallbacks below are NON-PRODUCTION dev/test conveniences that only fire when
# the env is unset (i.e. outside compose); `change-me` will not authenticate
# against a real instance. See docs/decisions/why-env-driven-db-credentials-2026-06-16.md.
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_AUTH = (
    os.getenv("NEO4J_USERNAME", "neo4j"),
    os.getenv("NEO4J_PASSWORD", os.getenv("NEO4J_AUTH_PASSWORD", "change-me")),
)

# PostGIS Configuration.
# Production builds POSTGIS_URI in docker-compose from POSTGRES_USER/PASSWORD/DB
# (POSTGRES_PASSWORD is fail-closed). The default below is a NON-PRODUCTION
# dev/test fallback: it targets the compose service name `postgis` (only
# resolvable inside the compose network), so it is inert in real deployments.
# Tests run outside docker override POSTGIS_URI explicitly (see backend/tests).
POSTGIS_URI = os.getenv("POSTGIS_URI", "postgresql://sentinel:sentinel@postgis:5432/sentinel")
ASYNC_POSTGIS_URI = POSTGIS_URI.replace("postgresql://", "postgresql+asyncpg://", 1)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class Neo4jConnection:
    def __init__(self, uri, user, pwd):
        # `notifications_disabled_classifications=["UNRECOGNIZED"]` silences
        # the "property key does not exist" / "relationship type does not
        # exist" warnings that the Link Graph emits when forward-compatible
        # queries hit empty graphs (e.g. /api/operational-entities/pending-
        # same-as on a fresh deployment with no POSSIBLY_SAME_AS edges yet).
        # These are not errors — they're Neo4j's static analysis flagging
        # references that haven't materialised yet. DEPRECATION warnings
        # are intentionally still surfaced so we catch real syntax drift.
        self.driver = GraphDatabase.driver(
            uri,
            auth=(user, pwd),
            notifications_disabled_classifications=["UNRECOGNIZED"],
        )

    def close(self):
        self.driver.close()

    def get_session(self):
        return self.driver.session()

class PostGISConnection:
    def __init__(self, dsn):
        self.dsn = dsn
        self.minconn = max(1, env_int("POSTGIS_POOL_MIN", 1))
        self.maxconn = max(self.minconn, env_int("POSTGIS_POOL_MAX", 10))
        self.acquire_retries = max(1, env_int("POSTGIS_POOL_ACQUIRE_RETRIES", 10))
        self.acquire_sleep_seconds = max(0.01, env_float("POSTGIS_POOL_ACQUIRE_SLEEP_SECONDS", 0.2))
        self._pool = None
        self._pool_lock = threading.Lock()

    def _connect_with_retry(self):
        import time
        last_error = None
        for i in range(5):
            try:
                return pool.ThreadedConnectionPool(
                    self.minconn,
                    self.maxconn,
                    self.dsn,
                    connection_factory=_VectorAwareConnection,
                )
            except psycopg2.OperationalError as e:
                last_error = e
                time.sleep(0.5)
        raise last_error

    def _get_pool(self):
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    self._pool = self._connect_with_retry()
                    logger.info("Initialized PostGIS connection pool min=%s max=%s", self.minconn, self.maxconn)
        return self._pool

    def get_connection(self):
        import time
        last_error = None
        for _ in range(self.acquire_retries):
            try:
                return self._get_pool().getconn()
            except pool.PoolError as e:
                last_error = e
                time.sleep(self.acquire_sleep_seconds)
        raise RuntimeError(f"PostGIS connection pool exhausted after {self.acquire_retries} attempts") from last_error

    def put_connection(self, conn, close=False):
        if conn is None:
            return
        pg_pool = self._get_pool()
        pg_pool.putconn(conn, close=close)

    def close(self):
        if self._pool is not None:
            with self._pool_lock:
                if self._pool is not None:
                    self._pool.closeall()
                    self._pool = None

    def reset_after_fork(self):
        """Drop the inherited connection pool after ``os.fork()``.

        libpq connections are not fork-safe: a child process that reuses a
        socket opened by its parent desyncs the wire protocol, which surfaces
        as ``DatabaseError: error with status PGRES_TUPLES_OK and no message
        from the libpq``. Celery's prefork pool forks one MainProcess into N
        children, so any pool built before the fork (e.g. by an import-time
        query) is shared across every child.

        Clearing ``_pool`` makes the next ``get_connection`` in this process
        lazily build a fresh pool owning its own connections. The inherited
        connection objects are dropped without an explicit ``closeall()`` —
        their sockets are shared with the parent (a forked fd points at the
        same TCP connection), so closing them here could tear down the
        parent's connection too. The parent owns their lifecycle.

        See docs/decisions/reset-db-pool-after-fork.md.
        """
        with self._pool_lock:
            self._pool = None

    @contextmanager
    def get_cursor(self, commit=False):
        conn = self.get_connection()
        close_conn = False
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            try:
                yield cursor
                if commit:
                    conn.commit()
                else:
                    conn.rollback()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            close_conn = True
            raise
        finally:
            self.put_connection(conn, close=close_conn)

# Global instances
neo4j_db = Neo4jConnection(NEO4J_URI, NEO4J_AUTH[0], NEO4J_AUTH[1])
postgis_db = PostGISConnection(POSTGIS_URI)

# Backwards compatibility
class DatabaseManager:
    def __init__(self):
        self.neo4j = neo4j_db
        self.postgis = postgis_db

    def get_session(self):
        return self.neo4j.get_session()

    def close(self):
        self.neo4j.close()
        self.postgis.close()

db = DatabaseManager()
