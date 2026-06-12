"""Offline unit tests for backend/terrain.py earth-curvature and antimeridian
handling. Regression guards for the 2026-06-12 audit fixes:

* line_of_sight used an observer-anchored d² curvature drop on a straight
  chord (huge false drop at the target end) — now the chord bulge
  ``(1-k)·d·(D-d)/2R`` which is zero at both endpoints.
* viewshed ADDED the curvature drop to distant terrain (making far ground
  appear HIGHER, falsely visible beyond an occluding ridge) — now subtracts.
* both walked interpolated/ray longitudes the long way around ±180°.

The DEM is monkeypatched out: ``sample_elevation`` is replaced by synthetic
terrain functions so no raster file is needed.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import terrain  # noqa: E402


def test_chord_bulge_zero_at_endpoints_max_at_midpoint():
    total = 50_000.0
    assert terrain._chord_bulge_m(0.0, total) == 0.0
    assert terrain._chord_bulge_m(total, total) == 0.0
    mid = terrain._chord_bulge_m(total / 2, total)
    expected = (1 - terrain.REFRACTION_K) * total ** 2 / (8 * terrain.EARTH_RADIUS_M)
    assert mid == pytest.approx(expected, rel=1e-9)
    assert mid > terrain._chord_bulge_m(total / 4, total)


def test_los_elevated_endpoints_visible_over_flat_terrain(monkeypatch):
    # 50 km over perfectly flat sea-level terrain, observer mast 40 m,
    # target mast 100 m: the chord clears the ~85 m mid-path earth bulge.
    # The old observer-anchored d² drop charged ~170 m at the target end
    # and falsely reported blocked.
    monkeypatch.setattr(terrain, "dem_available", lambda: True)
    monkeypatch.setattr(terrain, "sample_elevation", lambda lat, lon: 0.0)
    result = terrain.line_of_sight(
        24.0, 54.0, 24.0, 54.0 + 50_000.0 / terrain._meters_per_degree(24.0)[1],
        observer_height_m=40.0, target_height_m=100.0,
    )
    assert result is not None
    assert result["visible"] is True
    assert result["clearance_m"] > 0.0


def test_los_blocked_by_earth_bulge_between_low_endpoints(monkeypatch):
    # Same 50 km path with 2 m endpoints: the ~85 m bulge must block.
    monkeypatch.setattr(terrain, "dem_available", lambda: True)
    monkeypatch.setattr(terrain, "sample_elevation", lambda lat, lon: 0.0)
    result = terrain.line_of_sight(
        24.0, 54.0, 24.0, 54.0 + 50_000.0 / terrain._meters_per_degree(24.0)[1],
        observer_height_m=2.0, target_height_m=2.0,
    )
    assert result is not None
    assert result["visible"] is False
    assert result["clearance_m"] < 0.0


def test_los_antimeridian_samples_stay_near_dateline(monkeypatch):
    seen_lons: list[float] = []

    def _record(lat, lon):
        seen_lons.append(lon)
        return 0.0

    monkeypatch.setattr(terrain, "dem_available", lambda: True)
    monkeypatch.setattr(terrain, "sample_elevation", _record)
    result = terrain.line_of_sight(
        10.0, 179.9, 10.0, -179.9,
        observer_height_m=100.0, target_height_m=100.0,
    )
    assert result is not None
    assert seen_lons
    assert all(abs(abs(lon) - 180.0) < 0.2 for lon in seen_lons)
    assert all(-180.0 <= lon <= 180.0 for lon in seen_lons)


def _fake_dem(monkeypatch, elevation_fn, px_deg=0.00027):
    monkeypatch.setattr(terrain, "dem_available", lambda: True)
    monkeypatch.setattr(terrain, "sample_elevation", elevation_fn)
    src = types.SimpleNamespace(transform=types.SimpleNamespace(a=px_deg, e=-px_deg))
    monkeypatch.setattr(terrain, "_open_dem", lambda: src)


def test_viewshed_far_terrain_stays_hidden_behind_ridge(monkeypatch):
    # Observer (2 m) on flat ground; a 5 m ridge ring at ~5 km. Beyond the
    # ridge the flat ground must stay hidden: with curvature ADDED (old bug)
    # far terrain's effective height grew as d² and eventually out-sloped the
    # ridge, so the boundary ran out to the full 30 km radius.
    obs_lat, obs_lon = 24.0, 54.0

    def _elev(lat, lon):
        d = terrain.haversine_m(obs_lat, obs_lon, lat, lon)
        return 5.0 if 4_800.0 <= d <= 5_400.0 else 0.0

    _fake_dem(monkeypatch, _elev)
    result = terrain.viewshed(
        obs_lat, obs_lon,
        radius_m=30_000.0, observer_height_m=2.0, target_height_m=0.0,
        azimuth_step_deg=90.0,
    )
    assert result is not None
    ring = result["features"][0]["geometry"]["coordinates"][0]
    for lon, lat in ring[:-1]:
        d = terrain.haversine_m(obs_lat, obs_lon, lat, lon)
        assert d <= 6_000.0, f"boundary point {d:.0f} m out — far terrain leaked past the ridge"


def test_viewshed_antimeridian_boundary_wrapped(monkeypatch):
    _fake_dem(monkeypatch, lambda lat, lon: 0.0)
    result = terrain.viewshed(
        10.0, 179.999,
        radius_m=5_000.0, observer_height_m=50.0, target_height_m=0.0,
        azimuth_step_deg=90.0,
    )
    assert result is not None
    ring = result["features"][0]["geometry"]["coordinates"][0]
    assert all(-180.0 <= lon <= 180.0 for lon, _lat in ring)
