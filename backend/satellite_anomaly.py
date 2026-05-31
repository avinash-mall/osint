"""Satellite maneuver / decay detection + mission classification (offline).

Compares two epochs of mean orbital elements for the same object and flags:

* **maneuvers** — a change in period / inclination / eccentricity, or a RAAN
  shift beyond what J2 nodal precession predicts, larger than TLE fitting noise.
* **decay anomalies** — an abnormal rate of mean-motion increase (the object is
  losing altitude faster than routine drag).

Pure computation on parsed elements (`satellite_overpass.Tle.elements()`); no
network, no DB, no propagation — runs unchanged air-gapped (Hard rule #8).

Threshold values and the J2 RAAN-rate formula are clean-room implementations of
published references — Lemmens & Krag, "Two-Line-Elements-Based Maneuver
Detection Methods" (2014); Vallado, *Fundamentals of Astrodynamics and
Applications* §9.4 (J2 secular nodal regression). No ShadowBroker source copied.

R2 — `classify_mission` is a small public name-prefix lookup tagging a satellite
by mission family (military / SAR / navigation / EO / weather / science /
comms / commercial imaging).
"""

from __future__ import annotations

import math
from typing import Optional

# --- Maneuver thresholds (above TLE fitting noise, below routine secular drift) ---
MANEUVER_PERIOD_MIN = 0.1        # minutes
MANEUVER_INCLINATION_DEG = 0.05  # degrees
MANEUVER_ECCENTRICITY = 0.005
MANEUVER_RAAN_RESIDUAL_DEG = 0.5  # degrees, after subtracting expected J2 drift

# --- Decay threshold: mean-motion change rate (rev/day per day). Routine LEO
#     drag is ~0.001; an order of magnitude above that is an anomaly. ---
DECAY_MM_RATE_THRESHOLD = 0.01
DECAY_MIN_DT_DAYS = 0.5  # need ≥12 h between epochs for a meaningful rate

# WGS84 / Earth constants for J2 and altitude estimates.
_J2 = 1.08263e-3
_RE_KM = 6378.137
_MU = 398600.4418  # km^3 / s^2


def j2_raan_rate(inclination_deg: float, mean_motion_revday: float) -> float:
    """Expected RAAN (nodal) precession rate from J2, in degrees/day.

    Negative for prograde orbits (regression of the node). Vallado §9.4.
    """
    n_rad_s = mean_motion_revday * 2.0 * math.pi / 86400.0
    if n_rad_s <= 0:
        return 0.0
    a = (_MU / (n_rad_s ** 2)) ** (1.0 / 3.0)  # semi-major axis, km
    if a <= _RE_KM:
        return 0.0
    cos_i = math.cos(math.radians(inclination_deg))
    raan_rate_rad_s = -1.5 * n_rad_s * _J2 * (_RE_KM / a) ** 2 * cos_i
    return math.degrees(raan_rate_rad_s) * 86400.0  # rad/s → deg/day


def _period_min(mean_motion_revday: float) -> float:
    return 1440.0 / mean_motion_revday if mean_motion_revday > 0 else 0.0


def detect_maneuver(prev: dict, cur: dict) -> Optional[dict]:
    """Flag an orbital maneuver between two element sets for the same object.

    ``prev`` / ``cur`` are dicts from ``Tle.elements()``. Returns an alert dict
    with the triggering reasons, or ``None`` if all deltas are within noise.
    """
    keys = ("inclination_deg", "raan_deg", "eccentricity", "mean_motion_revday")
    if any(prev.get(k) is None or cur.get(k) is None for k in keys):
        return None

    reasons: list[str] = []

    d_period = abs(_period_min(cur["mean_motion_revday"]) - _period_min(prev["mean_motion_revday"]))
    if d_period > MANEUVER_PERIOD_MIN:
        reasons.append(f"period Δ{d_period:.3f} min")

    d_inc = abs(cur["inclination_deg"] - prev["inclination_deg"])
    if d_inc > MANEUVER_INCLINATION_DEG:
        reasons.append(f"inclination Δ{d_inc:.4f}°")

    d_ecc = abs(cur["eccentricity"] - prev["eccentricity"])
    if d_ecc > MANEUVER_ECCENTRICITY:
        reasons.append(f"eccentricity Δ{d_ecc:.6f}")

    # RAAN: only the residual beyond expected J2 precession counts as a maneuver.
    dt_days = _dt_days(prev, cur)
    if dt_days > 0:
        expected = j2_raan_rate(cur["inclination_deg"], cur["mean_motion_revday"]) * dt_days
        actual = (cur["raan_deg"] - prev["raan_deg"] + 180.0) % 360.0 - 180.0
        residual = abs(actual - expected)
        if residual > MANEUVER_RAAN_RESIDUAL_DEG:
            reasons.append(f"RAAN residual {residual:.3f}° (J2-corrected)")

    if not reasons:
        return None
    return {
        "norad_id": cur.get("norad_id"),
        "name": cur.get("name") or "",
        "type": "maneuver",
        "reasons": reasons,
        "epoch": cur.get("epoch"),
        "delta_period_min": round(d_period, 4),
        "delta_inclination_deg": round(d_inc, 5),
        "delta_eccentricity": round(d_ecc, 7),
    }


def detect_decay(prev: dict, cur: dict) -> Optional[dict]:
    """Flag an abnormal mean-motion increase rate (possible orbital decay)."""
    if prev.get("mean_motion_revday") is None or cur.get("mean_motion_revday") is None:
        return None
    dt_days = _dt_days(prev, cur)
    if dt_days < DECAY_MIN_DT_DAYS:
        return None
    mm_rate = (cur["mean_motion_revday"] - prev["mean_motion_revday"]) / dt_days
    if abs(mm_rate) <= DECAY_MM_RATE_THRESHOLD:
        return None
    cur_mm = cur["mean_motion_revday"]
    # Rough circular-orbit altitude from mean motion (km): a = (mu/n^2)^(1/3) - Re.
    n_rad_s = cur_mm * 2.0 * math.pi / 86400.0
    alt_km = ((_MU / (n_rad_s ** 2)) ** (1.0 / 3.0) - _RE_KM) if n_rad_s > 0 else 0.0
    return {
        "norad_id": cur.get("norad_id"),
        "name": cur.get("name") or "",
        "type": "decay_anomaly",
        "mm_rate_revday2": round(mm_rate, 6),
        "current_mean_motion": round(cur_mm, 4),
        "approx_alt_km": round(alt_km, 1),
        "epoch": cur.get("epoch"),
        "dt_days": round(dt_days, 2),
    }


def _dt_days(prev: dict, cur: dict) -> float:
    pe, ce = prev.get("epoch_ts"), cur.get("epoch_ts")
    if not pe or not ce:
        return 0.0
    return (ce - pe) / 86400.0


# ---------------------------------------------------------------------------
# R2 — mission classification (clean-room public name-prefix lookup)
# ---------------------------------------------------------------------------

# Ordered most-specific-first; matched as case-insensitive substring/prefix on
# the satellite name. Values are (mission, label).
_MISSION_RULES: list[tuple[str, str, str]] = [
    # Navigation constellations first — their names often also contain a generic
    # "USA nnn" designator, which must not pre-empt the more specific match.
    ("GPS", "navigation", "GPS Navigation"),
    ("NAVSTAR", "navigation", "GPS Navigation"),
    ("GALILEO", "navigation", "Galileo Navigation"),
    ("BEIDOU", "navigation", "BeiDou Navigation"),
    ("GLONASS", "navigation", "GLONASS Navigation"),
    ("NROL", "recon", "Classified NRO"),
    ("COSMOS", "military", "Russian/Soviet Military"),
    ("YAOGAN", "recon", "Chinese Recon"),
    ("LACROSSE", "sar", "Radar Recon"),
    ("ONYX", "sar", "Radar Recon"),
    ("USA ", "military", "US Military"),
    ("SENTINEL-1", "sar", "Copernicus SAR"),
    ("SENTINEL", "earth_observation", "Copernicus EO"),
    ("LANDSAT", "earth_observation", "Landsat EO"),
    ("WORLDVIEW", "commercial_imaging", "Maxar High-Res"),
    ("PLEIADES", "commercial_imaging", "Airbus Imaging"),
    ("ICEYE", "sar", "Commercial SAR"),
    ("CAPELLA", "sar", "Commercial SAR"),
    ("NOAA", "weather", "NOAA Weather"),
    ("METEOR", "weather", "Meteor Weather"),
    ("METOP", "weather", "MetOp Weather"),
    ("GOES", "weather", "GOES Weather"),
    ("STARLINK", "comms", "Starlink Comms"),
    ("ONEWEB", "comms", "OneWeb Comms"),
    ("IRIDIUM", "comms", "Iridium Comms"),
    ("INTELSAT", "comms", "Intelsat Comms"),
]


def classify_mission(name: Optional[str]) -> dict:
    """Tag a satellite by mission family from its name. Always returns a dict.

    Falls back to ``{"mission": "unknown", "label": "Unclassified"}`` when no
    rule matches — never raises.
    """
    if name:
        upper = name.upper()
        for token, mission, label in _MISSION_RULES:
            if token in upper:
                return {"mission": mission, "label": label}
    return {"mission": "unknown", "label": "Unclassified"}
