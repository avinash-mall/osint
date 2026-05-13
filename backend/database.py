import os
import logging
import threading
from neo4j import GraphDatabase
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Neo4j Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_AUTH = (
    os.getenv("NEO4J_USERNAME", "neo4j"),
    os.getenv("NEO4J_PASSWORD", os.getenv("NEO4J_AUTH_PASSWORD", "change-me")),
)

# PostGIS Configuration
# Default URI uses the docker-compose service name `postgis`, which resolves
# via docker DNS inside the compose network. The hardcoded IP `172.18.0.11`
# previously here was unstable across compose recreations; tests run outside
# docker should override POSTGIS_URI explicitly.
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
        self.driver = GraphDatabase.driver(uri, auth=(user, pwd))

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
                return pool.ThreadedConnectionPool(self.minconn, self.maxconn, self.dsn)
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
