"""Routing for the analytics router — OSRM HTTP client.

Routes are computed by a sidecar OSRM service (``ghcr.io/project-osrm/osrm-backend``)
mounted against a planet OSM extract. The backend talks to OSRM over HTTP at
``$OSRM_URL`` (defaults to ``http://osrm:5000``) — see the ``osrm`` service in
docker-compose. OSRM ships car-profile, MLD-algorithm routes on planet-scale
data in sub-second time and is fully air-gapped once the planet PBF has been
ingested via ``scripts/build_offline_osrm.py`` on a connected host.

The previous design loaded a pickled ``networkx`` graph entirely into memory.
That worked for AOI-scale graphs but is multi-terabyte for the planet, so it
was replaced by an out-of-process router. See
``docs/decisions/why-osrm-replaced-networkx.md``.

The module exposes ``osrm_available()`` (cheap, cached health probe) and
``compute_routes(...)`` (returns the same FeatureCollection shape the analytics
router has always emitted, with ``properties.mode = "osrm"``).
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_OSRM_URL = "http://osrm:5000"
HEALTH_CACHE_TTL_S = 5.0
ROUTE_TIMEOUT_S = 15.0


def osrm_url() -> str:
    return (os.getenv("OSRM_URL") or DEFAULT_OSRM_URL).rstrip("/")


_health_state: dict = {"ok": False, "checked_at": 0.0}


def osrm_available() -> bool:
    """True when the OSRM sidecar answers a trivial route within ~1 s.

    Result is cached for ``HEALTH_CACHE_TTL_S`` so the per-request capability
    probe and ``/api/analytics/capabilities`` do not hammer OSRM on every poll.
    """
    now = time.monotonic()
    if (now - _health_state["checked_at"]) < HEALTH_CACHE_TTL_S:
        return bool(_health_state["ok"])
    ok = False
    try:
        r = requests.get(
            f"{osrm_url()}/route/v1/driving/0,0;0.001,0.001",
            params={"overview": "false", "alternatives": "false", "steps": "false"},
            timeout=1.5,
        )
        if r.status_code == 200:
            body = r.json()
            ok = body.get("code") in {"Ok", "NoRoute"}
    except Exception as exc:  # pragma: no cover - exercised in air-gap tests
        logger.debug("osrm health probe failed: %s", exc)
        ok = False
    _health_state["ok"] = ok
    _health_state["checked_at"] = now
    return ok


def reset_osrm_health_cache() -> None:
    _health_state["ok"] = False
    _health_state["checked_at"] = 0.0


_STRATEGY_LABELS = {
    "shortest":       "shortest",
    "balanced":       "balanced",
    "least_exposure": "least exposure",
}


def _risk_label(option_idx: int) -> str:
    # OSRM ranks alternatives by total weighted duration. Without an exposure
    # raster baked into the OSRM profile, we surface the rank as the risk
    # label — option 1 is the primary route, 2/3 are detours.
    if option_idx == 1:
        return "primary"
    return f"alternative {option_idx}"


def compute_routes(
    obs_lat: float,
    obs_lon: float,
    dst_lat: float,
    dst_lon: float,
    *,
    strategy: Optional[str] = None,
) -> Optional[list[dict]]:
    """Return up to three OSRM driving routes between observer and destination.

    Strategy parameter is accepted for API compatibility but does not change
    OSRM's edge weights — all three alternatives are surfaced regardless, and
    the ``strategy`` field on each Feature is set to the caller's request (or
    ``"alternative"`` when no strategy was specified). True
    exposure-aware routing requires a custom Lua profile baked into the
    planet OSRM build; see ``docs/decisions/why-osrm-replaced-networkx.md`` for
    the trade-off.

    Returns ``None`` when OSRM is unreachable or has no path.
    """
    if not osrm_available():
        return None

    url = (
        f"{osrm_url()}/route/v1/driving/"
        f"{obs_lon:.6f},{obs_lat:.6f};{dst_lon:.6f},{dst_lat:.6f}"
    )
    params = {
        "alternatives": "3",
        "overview": "full",
        "geometries": "geojson",
        "annotations": "duration,distance",
        "steps": "false",
    }
    try:
        r = requests.get(url, params=params, timeout=ROUTE_TIMEOUT_S)
    except Exception as exc:
        logger.warning("osrm route request failed: %s", exc)
        return None
    if r.status_code != 200:
        logger.warning("osrm route HTTP %s: %s", r.status_code, r.text[:200])
        return None
    body = r.json()
    if body.get("code") != "Ok":
        # NoRoute / NoSegment / InvalidQuery — caller surfaces as no result.
        logger.info("osrm route non-Ok: code=%s message=%s", body.get("code"), body.get("message"))
        return None

    routes = body.get("routes") or []
    if not routes:
        return None

    label = _STRATEGY_LABELS.get(strategy or "", strategy or "alternative")
    out: list[dict] = []
    for idx, route in enumerate(routes, start=1):
        geom = route.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        length_m = float(route.get("distance") or 0.0)
        duration_s = float(route.get("duration") or 0.0)
        out.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "option": idx,
                "strategy": strategy or "alternative",
                "label": label,
                "length_m": length_m,
                "duration_minutes": duration_s / 60.0,
                "exposure": 0.0,
                "risk": _risk_label(idx),
            },
        })
    return out or None


EARTH_RADIUS_M = 6_371_008.8
# OSRM ships ``--max-table-size 100`` by default; bearings*rings + 1 source must
# stay under it. 16 bearings × 6 rings + 1 = 97 probes.
ISO_BEARINGS = 16
ISO_RINGS = 6


def _destination_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Great-circle forward: point ``distance_m`` along ``bearing_deg`` from (lat, lon)."""
    ang = distance_m / EARTH_RADIUS_M
    brg = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(ang) + math.cos(lat1) * math.sin(ang) * math.cos(brg))
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(ang) * math.cos(lat1),
        math.cos(ang) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def compute_isochrone(
    center_lat: float,
    center_lon: float,
    minutes: float,
    *,
    nominal_speed_kmh: float = 60.0,
) -> Optional[dict]:
    """Driving-time isochrone polygon around a center point via the OSRM matrix.

    Fires a ring of probe points outward along ``ISO_BEARINGS`` bearings at
    ``ISO_RINGS`` increasing radii, asks OSRM ``/table`` for the driving duration
    from the center to every probe in one request, then for each bearing keeps the
    farthest probe reachable within the time budget. Connecting those per-bearing
    extremes yields a star-shaped reachable polygon. Returns a single-Polygon
    FeatureCollection (``mode = "osrm"``) or ``None`` when OSRM is unreachable or
    nothing is reachable. See docs/decisions/why-isochrone-reachability.md.
    """
    if not osrm_available():
        return None
    if minutes <= 0:
        return None

    speed_mps = max(1.0, nominal_speed_kmh * 1000.0 / 3600.0)
    # Straight-line bound the matrix probes: time budget at nominal speed. OSRM
    # filters anything not actually reachable by road within the threshold.
    max_radius_m = minutes * 60.0 * speed_mps
    radii = [max_radius_m * (r / ISO_RINGS) for r in range(1, ISO_RINGS + 1)]
    bearings = [b * (360.0 / ISO_BEARINGS) for b in range(ISO_BEARINGS)]

    coords = [(center_lon, center_lat)]
    probe_meta: list[tuple[int, int]] = []  # (bearing_idx, ring_idx) per probe
    for bi, brg in enumerate(bearings):
        for ri, rad in enumerate(radii):
            plat, plon = _destination_point(center_lat, center_lon, brg, rad)
            coords.append((plon, plat))
            probe_meta.append((bi, ri))

    coord_str = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)
    url = f"{osrm_url()}/table/v1/driving/{coord_str}"
    params = {"sources": "0", "annotations": "duration"}
    try:
        r = requests.get(url, params=params, timeout=ROUTE_TIMEOUT_S)
    except Exception as exc:
        logger.warning("osrm isochrone table request failed: %s", exc)
        return None
    if r.status_code != 200:
        logger.warning("osrm isochrone table HTTP %s: %s", r.status_code, r.text[:200])
        return None
    body = r.json()
    if body.get("code") != "Ok":
        logger.info("osrm isochrone non-Ok: code=%s", body.get("code"))
        return None

    durations = (body.get("durations") or [[]])[0]  # seconds from source to each coord
    if not durations:
        return None
    threshold_s = minutes * 60.0

    # Per bearing, the farthest reachable ring index → that probe's coordinate.
    farthest: dict[int, tuple[int, float, float]] = {}
    for k, (bi, ri) in enumerate(probe_meta):
        dur = durations[k + 1]  # +1: index 0 is the source itself
        if dur is None or dur > threshold_s:
            continue
        plon, plat = coords[k + 1]
        if bi not in farthest or ri > farthest[bi][0]:
            farthest[bi] = (ri, plon, plat)

    if len(farthest) < 3:
        return None  # not enough reachable spokes to form a polygon

    ring: list[list[float]] = []
    for bi in range(ISO_BEARINGS):
        if bi in farthest:
            ring.append([farthest[bi][1], farthest[bi][2]])
        else:
            # Spoke unreachable: collapse to center so the polygon stays valid.
            ring.append([center_lon, center_lat])
    ring.append(ring[0])  # close

    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "minutes": minutes,
                "nominal_speed_kmh": nominal_speed_kmh,
                "reachable_spokes": len(farthest),
                "spokes": ISO_BEARINGS,
                "mode": "osrm",
            },
        }],
        "mode": "osrm",
    }
