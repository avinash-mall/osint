"""Unit tests for satellite maneuver/decay detection + mission classification (R1/R2).

Offline, no DB, no network. Drives the pure functions with parsed real TLE
elements and synthetic deltas.
"""

from __future__ import annotations

import math

import pytest

from satellite_anomaly import (
    classify_mission,
    detect_decay,
    detect_maneuver,
    j2_raan_rate,
)
from satellite_overpass import Tle

# Real published ISS (ZARYA) element set.
_ISS = Tle(
    name="ISS (ZARYA)",
    line1="1 25544U 98067A   20029.51782528 -.00000857  00000-0 -78211-5 0  9994",
    line2="2 25544  51.6442  21.4611 0005029 215.6310 144.4124 15.49521691210334",
)


def test_tle_elements_parse_matches_known_values():
    e = _ISS.elements()
    assert e is not None
    assert e["norad_id"] == 25544
    assert e["inclination_deg"] == pytest.approx(51.6442, abs=1e-4)
    assert e["raan_deg"] == pytest.approx(21.4611, abs=1e-4)
    assert e["eccentricity"] == pytest.approx(0.0005029, abs=1e-7)
    assert e["mean_motion_revday"] == pytest.approx(15.49521691, abs=1e-6)
    assert e["epoch_ts"] is not None


def test_j2_raan_rate_sign_and_magnitude():
    # ISS-like prograde LEO: nodal regression is negative, a few deg/day.
    rate = j2_raan_rate(51.6442, 15.495)
    assert rate < 0
    assert 3.0 < abs(rate) < 7.0


def _elem(**over):
    base = _ISS.elements().copy()
    base.update(over)
    return base


def test_no_maneuver_within_noise():
    prev = _elem()
    # Same epoch (dt=0 → RAAN check skipped) and only a within-noise inclination
    # nudge → no maneuver. The J2-corrected RAAN path is covered separately.
    cur = _elem(inclination_deg=prev["inclination_deg"] + 0.001)
    assert detect_maneuver(prev, cur) is None


def test_raan_drift_within_j2_is_not_a_maneuver():
    # Advance one day and move RAAN by exactly the expected J2 precession →
    # residual ~0 → not flagged.
    prev = _elem()
    expected = j2_raan_rate(prev["inclination_deg"], prev["mean_motion_revday"]) * 1.0
    new_raan = (prev["raan_deg"] + expected) % 360.0
    cur = _elem(epoch_ts=prev["epoch_ts"] + 86400, raan_deg=new_raan)
    assert detect_maneuver(prev, cur) is None


def test_raan_residual_wrapped_after_long_tle_gap():
    # 60 days of J2 drift on an ISS-like orbit is ~-300°; the wrapped
    # ``actual`` is +60° while ``expected`` stays at -300°. Without wrapping
    # the residual the difference is 360° → a false maneuver alert.
    prev = _elem()
    dt_days = 60.0
    expected = j2_raan_rate(prev["inclination_deg"], prev["mean_motion_revday"]) * dt_days
    new_raan = (prev["raan_deg"] + expected) % 360.0
    cur = _elem(epoch_ts=prev["epoch_ts"] + dt_days * 86400, raan_deg=new_raan)
    assert detect_maneuver(prev, cur) is None


def test_inclination_maneuver_flagged():
    prev = _elem()
    cur = _elem(epoch_ts=prev["epoch_ts"] + 86400, inclination_deg=prev["inclination_deg"] + 0.4)
    alert = detect_maneuver(prev, cur)
    assert alert is not None
    assert alert["type"] == "maneuver"
    assert any("inclination" in r for r in alert["reasons"])


def test_period_maneuver_flagged():
    prev = _elem()
    # Drop mean motion enough to shift the period > 0.1 min.
    cur = _elem(epoch_ts=prev["epoch_ts"] + 86400, mean_motion_revday=prev["mean_motion_revday"] - 0.02)
    alert = detect_maneuver(prev, cur)
    assert alert is not None
    assert any("period" in r for r in alert["reasons"])


def test_decay_anomaly_flagged_over_min_window():
    prev = _elem()
    # +0.05 rev/day over 1 day → rate 0.05 > 0.01 threshold.
    cur = _elem(epoch_ts=prev["epoch_ts"] + 86400, mean_motion_revday=prev["mean_motion_revday"] + 0.05)
    alert = detect_decay(prev, cur)
    assert alert is not None
    assert alert["type"] == "decay_anomaly"
    assert alert["mm_rate_revday2"] == pytest.approx(0.05, abs=1e-3)
    assert alert["approx_alt_km"] > 0


def test_decay_ignored_under_min_window():
    prev = _elem()
    cur = _elem(epoch_ts=prev["epoch_ts"] + 3600, mean_motion_revday=prev["mean_motion_revday"] + 0.05)
    assert detect_decay(prev, cur) is None  # only 1h < 12h minimum


def test_classify_mission():
    assert classify_mission("SENTINEL-1A")["mission"] == "sar"
    assert classify_mission("SENTINEL-2B")["mission"] == "earth_observation"
    assert classify_mission("NAVSTAR 81 (USA 319)")["mission"] == "navigation"
    assert classify_mission("COSMOS 2569")["mission"] == "military"
    assert classify_mission("STARLINK-1234")["mission"] == "comms"
    assert classify_mission("ICEYE-X12")["mission"] == "sar"
    assert classify_mission("SOME RANDOM CUBESAT")["mission"] == "unknown"
    assert classify_mission(None)["mission"] == "unknown"
