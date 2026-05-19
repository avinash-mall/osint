"""Backend test policy: unit tests stay offline; PostGIS suites skip cleanly."""

from __future__ import annotations

import os

import psycopg2
import pytest


def _postgis_available() -> bool:
    dsn = os.getenv("POSTGIS_URI", "postgresql://sentinel:sentinel@postgis:5432/sentinel")
    try:
        conn = psycopg2.connect(dsn, connect_timeout=1)
    except Exception:
        return False
    conn.close()
    return True


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _postgis_available():
        return
    skip = pytest.mark.skip(reason="PostGIS unavailable; integration test skipped")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
