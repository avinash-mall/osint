"""Unit test for the offline area dossier route (Tier C).

Offline: stubs ``database.postgis_db`` with a canned cursor so the route's
point-in-polygon + nearby-detection SQL is exercised end-to-end via TestClient
without a live PostGIS. Mirrors the stubbing approach in test_aois_router.py.
"""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(monkeypatch, *, country_row, det_row):
    cursor = MagicMock()
    cursor.execute = MagicMock()
    # /api/dossier issues two queries: country point-in-poly, then detection count.
    cursor.fetchone = MagicMock(side_effect=[country_row, det_row])
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cursor)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    postgis = MagicMock()
    postgis.get_cursor = MagicMock(return_value=cursor_cm)

    database_module = types.ModuleType("database")
    database_module.db = MagicMock()
    database_module.postgis_db = postgis
    monkeypatch.setitem(sys.modules, "database", database_module)

    sys.modules.pop("routers.imagery", None)
    router = importlib.import_module("routers.imagery").router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), cursor


def test_dossier_resolves_country_and_counts(monkeypatch):
    client, cursor = _client(
        monkeypatch,
        country_row={"name": "United Arab Emirates", "admin": "United Arab Emirates",
                     "iso_a3": "ARE", "pop_est": 9890000, "gdp_md_est": 421000},
        det_row={"n": 7},
    )
    resp = client.get("/api/dossier", params={"lat": 24.45, "lon": 54.38})
    assert resp.status_code == 200
    body = resp.json()
    assert body["country"]["iso_a3"] == "ARE"
    assert body["detections_within_25km"] == 7
    assert body["point"] == {"lat": 24.45, "lon": 54.38}
    assert body["source"] == "ne_countries (offline)"


def test_dossier_open_ocean_has_no_country(monkeypatch):
    client, cursor = _client(monkeypatch, country_row=None, det_row={"n": 0})
    resp = client.get("/api/dossier", params={"lat": 0.0, "lon": -140.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["country"] is None
    assert body["detections_within_25km"] == 0
