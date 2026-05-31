"""Unit tests for backend/satellite_overpass.py — offline, no DB, no network.

Strategy: validate the geometry by self-consistency rather than an external
oracle. The decisive checks are (a) geodetic↔ECEF round-trips, (b) an observer
standing on the computed sub-satellite point sees the satellite near zenith
(ties sub-point and elevation together), and (c) a pass is found over that point.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from satellite_overpass import (
    Tle,
    _ecef_to_geodetic,
    _geodetic_to_ecef,
    ground_track,
    parse_tle_text,
    predict_passes,
)

# Real published ISS (ZARYA) element set; epoch 2020 day 029.5178.
_ISS = Tle(
    name="ISS (ZARYA)",
    line1="1 25544U 98067A   20029.51782528 -.00000857  00000-0 -78211-5 0  9994",
    line2="2 25544  51.6442  21.4611 0005029 215.6310 144.4124 15.49521691210334",
)
_T = datetime(2020, 1, 29, 12, 25, 40, tzinfo=timezone.utc)  # ≈ TLE epoch


@pytest.mark.parametrize(
    "lat,lon,alt",
    [(0.0, 0.0, 0.0), (51.5, -0.12, 35.0), (-33.9, 151.2, 100.0), (89.0, 10.0, 0.0)],
)
def test_geodetic_ecef_roundtrip(lat, lon, alt):
    x, y, z = _geodetic_to_ecef(lat, lon, alt)
    rlat, rlon, ralt = _ecef_to_geodetic(x, y, z)
    assert rlat == pytest.approx(lat, abs=1e-6)
    assert rlon == pytest.approx(lon, abs=1e-6)
    assert ralt == pytest.approx(alt, abs=1e-3)


def test_parse_three_line_and_two_line():
    three = parse_tle_text(f"{_ISS.name}\n{_ISS.line1}\n{_ISS.line2}\n")
    assert len(three) == 1
    assert three[0].name == "ISS (ZARYA)"
    assert three[0].norad_id == 25544

    two = parse_tle_text(f"{_ISS.line1}\n{_ISS.line2}")
    assert len(two) == 1
    assert two[0].norad_id == 25544


def test_iss_subpoint_altitude_band():
    track = ground_track(_ISS, _T, _T + timedelta(seconds=1), step_s=60)
    assert len(track["altitudes_km"]) == 1
    alt = track["altitudes_km"][0]
    assert 350.0 < alt < 460.0, f"ISS altitude out of band: {alt} km"


def test_observer_on_subpoint_sees_zenith_and_pass():
    # Sub-point at _T:
    track = ground_track(_ISS, _T, _T + timedelta(seconds=1), step_s=60)
    sub_lon, sub_lat = track["coordinates"][0]

    # An observer standing on the sub-point should see the satellite near zenith
    # at _T, so a pass window straddling _T must exist with very high max elev.
    passes = predict_passes(
        _ISS, sub_lat, sub_lon, _T - timedelta(minutes=10), _T + timedelta(minutes=10),
        min_elevation_deg=10.0, step_s=15,
    )
    assert passes, "expected an overpass over the sub-point"
    p = max(passes, key=lambda x: x.max_elevation_deg)
    assert p.max_elevation_deg > 80.0, f"max elev too low: {p.max_elevation_deg}"
    assert p.aos <= p.max_elevation_time <= p.los
    assert p.duration_s > 0


def test_ground_track_moves():
    track = ground_track(_ISS, _T, _T + timedelta(minutes=5), step_s=60)
    coords = track["coordinates"]
    assert len(coords) >= 5
    # Consecutive sub-points must differ (the satellite is moving).
    assert coords[0] != coords[-1]
