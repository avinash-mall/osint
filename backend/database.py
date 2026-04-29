import os
from neo4j import GraphDatabase
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

# Neo4j Configuration
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_AUTH = (
    os.getenv("NEO4J_USERNAME", "neo4j"),
    os.getenv("NEO4J_PASSWORD", os.getenv("NEO4J_AUTH_PASSWORD", "change-me")),
)

# PostGIS Configuration
POSTGIS_URI = os.getenv("POSTGIS_URI", "postgresql://gotham:gotham@172.18.0.11:5432/gotham")
ASYNC_POSTGIS_URI = POSTGIS_URI.replace("postgresql://", "postgresql+asyncpg://", 1)

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

    def get_connection(self):
        import time
        last_error = None
        for i in range(5):
            try:
                return psycopg2.connect(self.dsn)
            except psycopg2.OperationalError as e:
                last_error = e
                time.sleep(0.5)
        raise last_error

    @contextmanager
    def get_cursor(self, commit=False):
        conn = self.get_connection()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            try:
                yield cursor
                if commit:
                    conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()
        finally:
            conn.close()

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

db = DatabaseManager()
