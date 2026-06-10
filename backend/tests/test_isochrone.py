"""Tests for the isochrone geometry helper in routing.compute_isochrone.

The OSRM matrix path needs a live sidecar (covered by the live smoke test); here
we verify the pure great-circle forward-projection that places the probe rings.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from routing import _destination_point, EARTH_RADIUS_M  # noqa: E402


def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def test_destination_distance_preserved():
    lat2, lon2 = _destination_point(25.0, 55.0, 90.0, 5000.0)
    assert abs(_haversine(25.0, 55.0, lat2, lon2) - 5000.0) < 1.0


def test_destination_due_north():
    lat2, lon2 = _destination_point(0.0, 0.0, 0.0, 1000.0)
    assert lat2 > 0  # north → latitude increases
    assert abs(lon2) < 1e-6  # longitude unchanged on a meridian


def test_destination_due_east():
    lat2, lon2 = _destination_point(0.0, 0.0, 90.0, 1000.0)
    assert lon2 > 0  # east → longitude increases
    assert abs(lat2) < 1e-3
