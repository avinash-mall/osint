"""Satellite overpass prediction from local TLEs (SGP4) — offline collection planning.

Given a Two-Line Element set (analyst-supplied, imported once over an air-gap)
and an observer point (an AOI centroid), this module predicts when a satellite
rises above a minimum elevation over that point, and samples its sub-satellite
ground track. It is pure computation — no network, no DB — so it runs unchanged
in an air-gapped deployment (Hard rule #8); only TLE *freshness* depends on the
operator re-importing newer elements.

The orbital model is SGP4 via the ``sgp4`` package (TEME frame output). We rotate
TEME→ECEF with GMST (IAU-82, polar motion/nutation ignored — sub-km, adequate for
overpass windows) and use closed-form WGS84 geodetic conversions for the
sub-point and the topocentric elevation angle.

Not to be confused with ``backend/tracker.py`` ("satellite-pass tracker"), which
associates *detections* across image acquisitions and has nothing to do with
orbital mechanics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sgp4.api import Satrec, jday

# WGS84 ellipsoid
_WGS84_A = 6378137.0  # semi-major axis, metres
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)


# ---------------------------------------------------------------------------
# TLE parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tle:
    """A parsed Two-Line Element set with its (optional) name line."""

    name: str
    line1: str
    line2: str

    @property
    def norad_id(self) -> Optional[int]:
        try:
            return int(self.line1[2:7])
        except (ValueError, IndexError):
            return None

    def satrec(self) -> Satrec:
        return Satrec.twoline2rv(self.line1, self.line2)

    def epoch(self) -> Optional[datetime]:
        """Element-set epoch (UTC) from line 1 columns 19-32 (YYDDD.DDDDDDDD)."""
        try:
            field = self.line1[18:32].strip()
            yy = int(field[:2])
            doy = float(field[2:])
            year = 2000 + yy if yy < 57 else 1900 + yy
            return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1.0)
        except (ValueError, IndexError):
            return None

    def elements(self) -> Optional[dict]:
        """Mean orbital elements from TLE line 2 (fixed columns, per the format spec).

        Returns ``{inclination_deg, raan_deg, eccentricity, mean_motion_revday}``
        plus ``epoch`` / ``epoch_ts``, or ``None`` if the line is malformed. Used
        by maneuver/decay detection (satellite_anomaly.py) — no propagation needed.
        """
        try:
            ln2 = self.line2
            inclination = float(ln2[8:16])
            raan = float(ln2[17:25])
            ecc = float("0." + ln2[26:33].strip())  # implied leading decimal point
            mean_motion = float(ln2[52:63])
            ep = self.epoch()
            return {
                "norad_id": self.norad_id,
                "name": self.name,
                "inclination_deg": inclination,
                "raan_deg": raan,
                "eccentricity": ecc,
                "mean_motion_revday": mean_motion,
                "epoch": ep.isoformat() if ep else None,
                "epoch_ts": ep.timestamp() if ep else None,
            }
        except (ValueError, IndexError):
            return None


def parse_tle_text(text: str) -> list[Tle]:
    """Parse 2-line or 3-line (name + 2 lines) TLE blocks from raw text.

    Tolerant of blank lines and a leading name line. Lines that don't look like
    TLE element lines (must start with '1 ' / '2 ') are treated as name lines.
    """
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out: list[Tle] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if ln.startswith("1 ") and i + 1 < n and lines[i + 1].startswith("2 "):
            out.append(Tle(name="", line1=ln, line2=lines[i + 1]))
            i += 2
        elif (
            i + 2 < n
            and lines[i + 1].startswith("1 ")
            and lines[i + 2].startswith("2 ")
        ):
            out.append(Tle(name=ln.strip(), line1=lines[i + 1], line2=lines[i + 2]))
            i += 3
        else:
            # Unrecognised line — skip it rather than fabricate an element set.
            i += 1
    return out


# ---------------------------------------------------------------------------
# Frame / geodetic math (clean-room, public formulas)
# ---------------------------------------------------------------------------


def _gmst_rad(jd_ut1: float) -> float:
    """Greenwich Mean Sidereal Time (radians), IAU-1982 polynomial."""
    t = (jd_ut1 - 2451545.0) / 36525.0
    gmst_sec = (
        67310.54841
        + (876600.0 * 3600.0 + 8640184.812866) * t
        + 0.093104 * t * t
        - 6.2e-6 * t * t * t
    )
    return (math.radians(gmst_sec / 240.0)) % (2.0 * math.pi)


def _teme_to_ecef(r_teme: tuple[float, float, float], gmst: float) -> tuple[float, float, float]:
    """Rotate a TEME position vector to ECEF about Z by GMST (R3(gmst))."""
    x, y, z = r_teme
    cg, sg = math.cos(gmst), math.sin(gmst)
    return (x * cg + y * sg, -x * sg + y * cg, z)


def _ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """ECEF metres → (lat_deg, lon_deg, alt_m) via Bowring's closed-form method."""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p < 1e-9:  # at a pole
        lat = math.copysign(math.pi / 2.0, z)
        alt = abs(z) - _WGS84_A * math.sqrt(1.0 - _WGS84_E2)
        return math.degrees(lat), math.degrees(lon), alt
    b = _WGS84_A * math.sqrt(1.0 - _WGS84_E2)
    ep2 = (_WGS84_A**2 - b**2) / b**2
    theta = math.atan2(z * _WGS84_A, p * b)
    lat = math.atan2(
        z + ep2 * b * math.sin(theta) ** 3,
        p - _WGS84_E2 * _WGS84_A * math.cos(theta) ** 3,
    )
    n = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * math.sin(lat) ** 2)
    alt = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), alt


def _geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float = 0.0) -> tuple[float, float, float]:
    """(lat_deg, lon_deg, alt_m) → ECEF metres."""
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    n = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * math.sin(lat) ** 2)
    x = (n + alt_m) * math.cos(lat) * math.cos(lon)
    y = (n + alt_m) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - _WGS84_E2) + alt_m) * math.sin(lat)
    return x, y, z


def _elevation_deg(obs_ecef: tuple[float, float, float], sat_ecef: tuple[float, float, float],
                   obs_lat_deg: float, obs_lon_deg: float) -> float:
    """Topocentric elevation angle (degrees) of ``sat`` seen from ``obs``."""
    rx = sat_ecef[0] - obs_ecef[0]
    ry = sat_ecef[1] - obs_ecef[1]
    rz = sat_ecef[2] - obs_ecef[2]
    rng = math.sqrt(rx * rx + ry * ry + rz * rz)
    if rng < 1e-6:
        return 90.0
    lat, lon = math.radians(obs_lat_deg), math.radians(obs_lon_deg)
    # Local "up" (ENU z-axis) at the observer.
    ux = math.cos(lat) * math.cos(lon)
    uy = math.cos(lat) * math.sin(lon)
    uz = math.sin(lat)
    sin_elev = (rx * ux + ry * uy + rz * uz) / rng
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_elev))))


# ---------------------------------------------------------------------------
# Propagation
# ---------------------------------------------------------------------------


def _subpoint_and_elevation(
    sat: Satrec, when: datetime, obs_ecef: tuple[float, float, float],
    obs_lat: float, obs_lon: float,
) -> tuple[float, float, float, float]:
    """Return (sub_lat, sub_lon, alt_km, elevation_deg) for ``sat`` at ``when`` (UTC)."""
    when = when.astimezone(timezone.utc)
    jd, fr = jday(
        when.year, when.month, when.day,
        when.hour, when.minute, when.second + when.microsecond / 1e6,
    )
    err, r_teme, _v = sat.sgp4(jd, fr)
    if err != 0:
        raise ValueError(f"SGP4 propagation error code {err}")
    gmst = _gmst_rad(jd + fr)
    ecef_km = _teme_to_ecef(r_teme, gmst)
    sat_ecef_m = (ecef_km[0] * 1000.0, ecef_km[1] * 1000.0, ecef_km[2] * 1000.0)
    sub_lat, sub_lon, alt_m = _ecef_to_geodetic(*sat_ecef_m)
    elev = _elevation_deg(obs_ecef, sat_ecef_m, obs_lat, obs_lon)
    return sub_lat, sub_lon, alt_m / 1000.0, elev


@dataclass
class Pass:
    """A single overpass window over the observer."""

    aos: datetime            # acquisition of signal (rise above min elevation)
    los: datetime            # loss of signal (set below min elevation)
    max_elevation_deg: float
    max_elevation_time: datetime
    duration_s: float

    def to_dict(self) -> dict:
        return {
            "aos": self.aos.isoformat(),
            "los": self.los.isoformat(),
            "max_elevation_deg": round(self.max_elevation_deg, 2),
            "max_elevation_time": self.max_elevation_time.isoformat(),
            "duration_s": round(self.duration_s, 1),
        }


def predict_passes(
    tle: Tle,
    obs_lat: float,
    obs_lon: float,
    start: datetime,
    end: datetime,
    *,
    min_elevation_deg: float = 10.0,
    step_s: int = 30,
) -> list[Pass]:
    """Predict overpasses of ``tle`` above ``min_elevation_deg`` over the observer.

    Walks the [start, end) window at ``step_s`` granularity, grouping contiguous
    samples where elevation ≥ threshold into passes. AOS/LOS are the first/last
    in-window samples of each group (coarse to ±step_s — adequate for planning).
    """
    sat = tle.satrec()
    obs_ecef = _geodetic_to_ecef(obs_lat, obs_lon, 0.0)
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)

    passes: list[Pass] = []
    cur: Optional[dict] = None
    t = start
    delta = timedelta(seconds=step_s)
    while t < end:
        try:
            _lat, _lon, _alt, elev = _subpoint_and_elevation(sat, t, obs_ecef, obs_lat, obs_lon)
        except ValueError:
            t += delta
            cur = None
            continue
        if elev >= min_elevation_deg:
            if cur is None:
                cur = {"aos": t, "los": t, "max_elev": elev, "max_t": t}
            else:
                cur["los"] = t
                if elev > cur["max_elev"]:
                    cur["max_elev"] = elev
                    cur["max_t"] = t
        elif cur is not None:
            passes.append(
                Pass(
                    aos=cur["aos"], los=cur["los"],
                    max_elevation_deg=cur["max_elev"], max_elevation_time=cur["max_t"],
                    duration_s=(cur["los"] - cur["aos"]).total_seconds(),
                )
            )
            cur = None
        t += delta
    if cur is not None:
        passes.append(
            Pass(
                aos=cur["aos"], los=cur["los"],
                max_elevation_deg=cur["max_elev"], max_elevation_time=cur["max_t"],
                duration_s=(cur["los"] - cur["aos"]).total_seconds(),
            )
        )
    return passes


def ground_track(
    tle: Tle, start: datetime, end: datetime, *, step_s: int = 60,
) -> dict:
    """Sample the sub-satellite point over [start, end) as a GeoJSON-ish dict.

    Returns ``{"coordinates": [[lon, lat], ...], "altitudes_km": [...]}``.
    Antimeridian splitting is left to the renderer; coordinates are raw lon/lat.
    """
    sat = tle.satrec()
    obs_ecef = _geodetic_to_ecef(0.0, 0.0, 0.0)  # unused for sub-point, reuse helper
    coords: list[list[float]] = []
    alts: list[float] = []
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    t = start
    delta = timedelta(seconds=step_s)
    while t < end:
        try:
            lat, lon, alt_km, _elev = _subpoint_and_elevation(sat, t, obs_ecef, 0.0, 0.0)
        except ValueError:
            t += delta
            continue
        coords.append([lon, lat])
        alts.append(round(alt_km, 2))
        t += delta
    return {"coordinates": coords, "altitudes_km": alts}
