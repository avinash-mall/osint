"""Satellite overpass / collection-window prediction routes.

Thin HTTP surface over [backend/satellite_overpass.py](../satellite_overpass.py).
TLEs are analyst-supplied and stored in PostGIS (``satellite_tles``); prediction
is pure computation on read, so the whole feature works air-gapped (Hard rule
#8). Observer points come from an existing AOI centroid or explicit lat/lon, so
this composes with [backend/routers/aois.py](aois.py).

The session middleware ([main.py](../main.py)) gates the mutating verbs
automatically — see
[docs/conventions/adding-a-new-router.md](../../docs/conventions/adding-a-new-router.md).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from database import postgis_db
from platform_schema import ensure_satellite_tables
from satellite_overpass import Tle, ground_track, parse_tle_text, predict_passes
from schemas import OverpassRequest, TleImportRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/satellites", tags=["satellites"])


def _parse_iso(value: Optional[str], default: datetime) -> datetime:
    if not value:
        return default
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid ISO8601 datetime: {value}")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_tles(norad_ids: Optional[list[int]] = None) -> list[Tle]:
    ensure_satellite_tables()
    sql = "SELECT norad_id, name, line1, line2 FROM satellite_tles"
    params: tuple = ()
    if norad_ids:
        sql += " WHERE norad_id = ANY(%s)"
        params = (list(norad_ids),)
    sql += " ORDER BY norad_id"
    with postgis_db.get_cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return [Tle(name=r["name"], line1=r["line1"], line2=r["line2"]) for r in rows]


def _resolve_observer(req: OverpassRequest) -> tuple[float, float]:
    if req.aoi_id is not None:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT ST_Y(ST_Centroid(geom)) AS lat, ST_X(ST_Centroid(geom)) AS lon "
                "FROM aois WHERE id = %s",
                (req.aoi_id,),
            )
            row = cursor.fetchone()
        if not row or row["lat"] is None:
            raise HTTPException(status_code=404, detail=f"AOI {req.aoi_id} not found")
        return float(row["lat"]), float(row["lon"])
    if req.lat is not None and req.lon is not None:
        return float(req.lat), float(req.lon)
    raise HTTPException(status_code=400, detail="provide aoi_id or both lat and lon")


@router.get("/tle")
def list_tles():
    """List stored TLEs (most-recent import per NORAD id)."""
    ensure_satellite_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            "SELECT norad_id, name, epoch, source, imported_at "
            "FROM satellite_tles ORDER BY norad_id"
        )
        rows = cursor.fetchall()
    return {"tles": [dict(r) for r in rows], "count": len(rows)}


@router.post("/tle", status_code=201)
def import_tle(body: TleImportRequest):
    """Import one or many TLE sets (air-gap upload). Upserts by NORAD id."""
    ensure_satellite_tables()
    parsed = parse_tle_text(body.text)
    if not parsed:
        raise HTTPException(status_code=400, detail="no valid TLE sets found in text")
    stored = 0
    with postgis_db.get_cursor(commit=True) as cursor:
        for tle in parsed:
            norad = tle.norad_id
            if norad is None:
                continue
            cursor.execute(
                """
                INSERT INTO satellite_tles (norad_id, name, line1, line2, epoch, source, imported_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (norad_id) DO UPDATE SET
                    name = EXCLUDED.name, line1 = EXCLUDED.line1, line2 = EXCLUDED.line2,
                    epoch = EXCLUDED.epoch, source = EXCLUDED.source, imported_at = now()
                """,
                (norad, tle.name, tle.line1, tle.line2, tle.epoch(), body.source),
            )
            stored += 1
    return {"success": True, "imported": stored}


@router.post("/passes")
def predict_overpasses(req: OverpassRequest):
    """Predict overpasses of the selected satellites over an AOI/point."""
    obs_lat, obs_lon = _resolve_observer(req)
    tles = _load_tles(req.norad_ids)
    if not tles:
        raise HTTPException(status_code=404, detail="no TLEs stored; import via POST /api/satellites/tle")
    start = _parse_iso(req.start, datetime.now(timezone.utc))
    end = _parse_iso(req.end, start + timedelta(hours=req.hours))
    if end <= start:
        raise HTTPException(status_code=400, detail="end must be after start")

    results = []
    for tle in tles:
        try:
            passes = predict_passes(
                tle, obs_lat, obs_lon, start, end,
                min_elevation_deg=req.min_elevation_deg, step_s=req.step_s,
            )
        except Exception as exc:  # stale/garbage element set
            logger.warning("overpass: prediction failed for %s: %s", tle.norad_id, exc)
            continue
        if passes:
            results.append({
                "norad_id": tle.norad_id,
                "name": tle.name,
                "passes": [p.to_dict() for p in passes],
            })
    return {
        "observer": {"lat": obs_lat, "lon": obs_lon},
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "satellites": results,
    }


@router.get("/ground-track/{norad_id}")
def get_ground_track(norad_id: int, hours: float = 1.5, step_s: int = 60):
    """Sub-satellite ground track for one NORAD id over the next ``hours``."""
    tles = _load_tles([norad_id])
    if not tles:
        raise HTTPException(status_code=404, detail=f"no stored TLE for NORAD {norad_id}")
    start = datetime.now(timezone.utc)
    track = ground_track(tles[0], start, start + timedelta(hours=hours), step_s=step_s)
    return {"norad_id": norad_id, "name": tles[0].name, **track}
