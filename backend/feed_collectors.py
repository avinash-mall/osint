"""HTTP feed polling helpers.

Used by ``worker.tick_feed_poll`` to pull events from registered
``feed_sources`` rows. Each parser returns a list of normalised event dicts:

    {
      "event_type": str,
      "latitude": float | None,
      "longitude": float | None,
      "observed_at": str | None,  # ISO-8601, will COALESCE to NOW() at insert
      "payload": dict,
    }

TCP / UDP / WebSocket / serial protocols are out of scope (HTTP-only collector).
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 8.0
MAX_EVENTS_PER_POLL = 500


def _coerce_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _parse_json_events(body: dict | list) -> list[dict]:
    """Parser: ``json`` — accepts either ``{"events": [...]}`` or a bare list."""
    if isinstance(body, dict):
        events = body.get("events") or body.get("items") or []
    elif isinstance(body, list):
        events = body
    else:
        return []
    out: list[dict] = []
    for evt in events:
        if not isinstance(evt, dict):
            continue
        lat = _coerce_float(evt.get("latitude") or evt.get("lat"))
        lon = _coerce_float(evt.get("longitude") or evt.get("lon"))
        out.append({
            "event_type": _coerce_str(evt.get("event_type") or evt.get("type")) or "observation",
            "latitude": lat,
            "longitude": lon,
            "observed_at": _coerce_str(evt.get("observed_at") or evt.get("time") or evt.get("timestamp")),
            "payload": {k: v for k, v in evt.items() if k not in {"latitude", "longitude", "lat", "lon"}},
        })
    return out


def _parse_geojson(body: dict) -> list[dict]:
    """Parser: ``geojson`` — FeatureCollection with Point/Polygon features."""
    if not isinstance(body, dict):
        return []
    features = body.get("features") or []
    out: list[dict] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        lat, lon = None, None
        if geom.get("type") == "Point" and len(coords) >= 2:
            lon, lat = _coerce_float(coords[0]), _coerce_float(coords[1])
        elif geom.get("type") in {"Polygon", "MultiPolygon"} and coords:
            # Use the first ring's first vertex as a representative point.
            try:
                ring = coords[0][0] if geom["type"] == "MultiPolygon" else coords[0]
                if ring and len(ring[0]) >= 2:
                    lon, lat = _coerce_float(ring[0][0]), _coerce_float(ring[0][1])
            except (TypeError, IndexError):
                pass
        out.append({
            "event_type": _coerce_str(props.get("event_type") or props.get("type")) or "feature",
            "latitude": lat,
            "longitude": lon,
            "observed_at": _coerce_str(props.get("observed_at") or props.get("time")),
            "payload": {**props, "geometry": geom},
        })
    return out


def _parse_adsb_basestation(text: str) -> list[dict]:
    """Parser: ``adsb_basestation`` — newline-delimited SBS-1 CSV.

    Each MSG line is comma-separated: ``MSG,<transmission>,<session>,<aircraft>,
    <hex_ident>,<flight_id>,<gen_date>,<gen_time>,<log_date>,<log_time>,
    <callsign>,<altitude>,<gs>,<track>,<lat>,<lon>,...``
    """
    out: list[dict] = []
    for line in (text or "").splitlines():
        parts = line.strip().split(",")
        if len(parts) < 16 or parts[0] != "MSG":
            continue
        lat = _coerce_float(parts[14]) if parts[14] else None
        lon = _coerce_float(parts[15]) if parts[15] else None
        if lat is None or lon is None:
            continue
        observed_at = None
        if parts[6] and parts[7]:
            observed_at = f"{parts[6]}T{parts[7]}Z"
        out.append({
            "event_type": "adsb_track",
            "latitude": lat,
            "longitude": lon,
            "observed_at": observed_at,
            "payload": {
                "hex_ident": parts[4],
                "callsign": parts[10].strip() or None,
                "altitude_ft": _coerce_float(parts[11]) if parts[11] else None,
                "ground_speed_kt": _coerce_float(parts[12]) if parts[12] else None,
                "track_deg": _coerce_float(parts[13]) if parts[13] else None,
            },
        })
    return out


def poll_http_feed(source: dict) -> list[dict]:
    """Fetch ``source['endpoint']`` and return parsed events.

    Raises on transport errors so the caller can mark the feed as ``error``.
    The parser is chosen via ``source['parser']`` (default: ``json``).
    """
    endpoint = source.get("endpoint")
    if not endpoint:
        raise ValueError("feed_source.endpoint is empty")

    response = requests.get(endpoint, timeout=DEFAULT_TIMEOUT_S)
    response.raise_for_status()

    parser = (source.get("parser") or "json").strip().lower()
    if parser == "adsb_basestation":
        events: Iterable[dict] = _parse_adsb_basestation(response.text)
    elif parser == "geojson":
        events = _parse_geojson(response.json())
    else:
        events = _parse_json_events(response.json())

    events_list = list(events)[:MAX_EVENTS_PER_POLL]
    logger.info("feed %s (%s): parsed %d events", source.get("name"), parser, len(events_list))
    return events_list
