"""
tracker.py — Hungarian-assignment multi-pass detection tracker for satellite OSINT.

Public API:
    update_tracks_for_pass(pass_id, *, postgis_db) -> dict
    reprocess_all_tracks(*, postgis_db, since=None) -> dict
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from math import atan2, degrees, radians, sin, cos, sqrt

import numpy as np
from pyproj import Geod
from scipy.optimize import linear_sum_assignment

from threat_assessment import category_for_class, assess_detection_threat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

V_MAX: dict[str, float] = {
    "maritime":       16.0,   # ~30 kt
    "ground":         22.0,   # ~80 km/h
    "air":            14.0,   # on-ground aircraft taxi only
    "infrastructure":  0.0,   # pinned; never moves
    "default":        16.0,   # unknown / combat / fallback
}

MATCH_THRESHOLD = 1.5
INIT_CONF_THRESHOLD = 0.4
MAX_TRACK_AGE_DAYS = 14
MAX_MISS_COUNT = 3
CLOUD_COVER_OCCLUSION = 0.7

# Cost weights
ALPHA = 1.0   # spatial distance weight
BETA  = 0.6   # class penalty weight
GAMMA = 0.2   # confidence penalty weight

_GEOD = Geod(ellps="WGS84")

# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------

def _tracker_category(class_name: str) -> str:
    """Map a detection class to one of the V_MAX category keys."""
    raw = category_for_class(class_name)  # may return "combat", "unknown", etc.
    if raw in V_MAX:
        return raw
    return "default"


def _v_max(category: str) -> float:
    return V_MAX.get(category, V_MAX["default"])


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the geodesic distance in metres between two WGS-84 points."""
    _, _, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
    return abs(dist)


def _predict_position(track: dict, delta_t_seconds: float) -> tuple[float, float]:
    """Return (lat, lon) of predicted track position after delta_t_seconds."""
    lat = track["lat"]
    lon = track["lon"]
    vel = track.get("last_velocity") or {}
    if isinstance(vel, str):
        try:
            vel = json.loads(vel)
        except Exception:
            vel = {}

    vx = float(vel.get("vx_mps", 0.0))
    vy = float(vel.get("vy_mps", 0.0))
    speed = sqrt(vx * vx + vy * vy)

    if track.get("obs_count", 0) >= 2 and speed > 0:
        az_deg = degrees(atan2(vx, vy))
        dist_m = speed * delta_t_seconds
        new_lon, new_lat, _ = _GEOD.fwd(lon, lat, az_deg, dist_m)
        return float(new_lat), float(new_lon)

    return lat, lon


def _velocity_from_observations(
    prev_lon: float, prev_lat: float,
    new_lon: float, new_lat: float,
    dt_seconds: float,
) -> dict:
    """Compute ENU velocity dict {vx_mps, vy_mps} from two observations."""
    if dt_seconds <= 0:
        return {"vx_mps": 0.0, "vy_mps": 0.0}

    az12, _, dist = _GEOD.inv(prev_lon, prev_lat, new_lon, new_lat)
    if dist <= 0:
        return {"vx_mps": 0.0, "vy_mps": 0.0}

    az_rad = radians(az12)
    speed = dist / dt_seconds
    return {
        "vx_mps": speed * sin(az_rad),
        "vy_mps": speed * cos(az_rad),
    }


# ---------------------------------------------------------------------------
# Cost function
# ---------------------------------------------------------------------------

def _compute_cost(
    track: dict,
    det: dict,
    delta_t_seconds: float,
) -> float:
    """Return cost for assigning detection to track, or np.inf if outside gate."""
    category = track.get("category") or "default"
    vm = _v_max(category)
    r_gate = vm * delta_t_seconds * 1.25
    if r_gate == 0:
        r_gate = 10.0  # minimum gate for infrastructure / stationary tracks

    pred_lat, pred_lon = _predict_position(track, delta_t_seconds)
    dist_m = _haversine_metres(pred_lat, pred_lon, det["lat"], det["lon"])
    d_norm = dist_m / r_gate

    if d_norm > 1.0:
        return np.inf

    # Class penalty
    track_class = (track.get("primary_class") or "").lower()
    det_class = (det.get("class") or "").lower()
    if track_class == det_class:
        class_penalty = 0.0
    elif _tracker_category(track_class) == _tracker_category(det_class):
        class_penalty = 0.4
    else:
        class_penalty = 1.0

    conf_penalty = (1.0 - float(det.get("confidence", 0.0))) * 0.2

    return ALPHA * d_norm + BETA * class_penalty + GAMMA * conf_penalty


# ---------------------------------------------------------------------------
# Core public function
# ---------------------------------------------------------------------------

def update_tracks_for_pass(pass_id: int, *, postgis_db) -> dict:
    """Run Hungarian matching for detections in pass_id against active tracks.

    Returns dict: {assigned, new_tracks, missed_tracks, lost_tracks}
    """
    stats: dict[str, int] = {
        "assigned": 0,
        "new_tracks": 0,
        "missed_tracks": 0,
        "lost_tracks": 0,
    }

    # ------------------------------------------------------------------
    # 1. Load pass metadata
    # ------------------------------------------------------------------
    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT id, acquisition_time,
                   COALESCE(cloud_cover, 0) AS cloud_cover,
                   footprint
            FROM satellite_passes WHERE id = %s
            """,
            (pass_id,),
        )
        pass_row = cur.fetchone()

    if pass_row is None:
        logger.warning("tracker: pass %s not found", pass_id)
        return stats

    acq_time = pass_row["acquisition_time"]
    if acq_time is None:
        return stats  # can't track without a timestamp
    # ensure timezone-aware
    if acq_time.tzinfo is None:
        acq_time = acq_time.replace(tzinfo=timezone.utc)
    footprint_wkb = pass_row.get("footprint")  # may be None
    cloud_cover: float = float(pass_row["cloud_cover"] or 0.0)
    occluded_by_cloud = cloud_cover > CLOUD_COVER_OCCLUSION

    # ------------------------------------------------------------------
    # 2. Load candidate tracks
    # ------------------------------------------------------------------
    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT dt.id, dt.track_uid, dt.primary_class, dt.category,
                   dt.status, dt.pinned,
                   dt.obs_count, dt.miss_count, dt.last_seen,
                   ST_X(dt.last_centroid) AS lon,
                   ST_Y(dt.last_centroid) AS lat,
                   dt.last_velocity
            FROM detection_tracks dt
            WHERE dt.status IN ('tentative', 'confirmed', 'coast', 'pinned')
              AND (dt.last_seen >= NOW() - INTERVAL '14 days' OR dt.pinned = TRUE)
            """,
        )
        tracks = [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # 3. Load new detections
    # ------------------------------------------------------------------
    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT id, class, confidence,
                   ST_Y(centroid) AS lat,
                   ST_X(centroid) AS lon
            FROM detections WHERE pass_id = %s
            """,
            (pass_id,),
        )
        detections = [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # 4. No detections → age missed tracks
    # ------------------------------------------------------------------
    if not detections:
        _age_unmatched_tracks(
            tracks, acq_time, occluded_by_cloud, pass_row, stats, postgis_db
        )
        return stats

    # ------------------------------------------------------------------
    # 5. No candidate tracks → seed tentative tracks
    # ------------------------------------------------------------------
    if not tracks:
        _create_new_tracks(detections, acq_time, pass_id, postgis_db, stats)
        return stats

    # ------------------------------------------------------------------
    # 6. Build cost matrix  (n_tracks × (n_dets + 1))
    # ------------------------------------------------------------------
    n_tracks = len(tracks)
    n_dets = len(detections)

    cost_matrix = np.full((n_tracks, n_dets + 1), MATCH_THRESHOLD, dtype=float)
    # Last column = no-match sentinel at MATCH_THRESHOLD

    for ti, track in enumerate(tracks):
        last_seen: datetime = track["last_seen"]
        if last_seen.tzinfo is None:
            last_seen_aware = last_seen.replace(tzinfo=timezone.utc)
        else:
            last_seen_aware = last_seen

        delta_t = (acq_time - last_seen_aware).total_seconds()
        if delta_t < 0:
            delta_t = 0.0  # guard: pass is older than last_seen

        for di, det in enumerate(detections):
            cost = _compute_cost(track, det, delta_t)
            # Replace inf with large finite value so scipy can solve
            cost_matrix[ti, di] = cost if not np.isinf(cost) else 1e9

    # ------------------------------------------------------------------
    # 7. Hungarian assignment
    # ------------------------------------------------------------------
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    # ------------------------------------------------------------------
    # 8. Parse assignments
    # ------------------------------------------------------------------
    matched_track_ids: set[int] = set()
    matched_det_indices: set[int] = set()
    assignments: list[tuple[dict, dict, float]] = []  # (track, det, cost)

    for ri, ci in zip(row_ind, col_ind):
        track = tracks[ri]
        cost_val = cost_matrix[ri, ci]

        if ci == n_dets:
            # No-match column — track is unmatched
            continue
        if cost_val >= MATCH_THRESHOLD:
            # Bad assignment; reject
            continue

        matched_track_ids.add(ri)
        matched_det_indices.add(ci)
        assignments.append((track, detections[ci], cost_val))

    # Unmatched detections: indices not chosen by the solver
    unmatched_det_indices = [
        i for i in range(n_dets) if i not in matched_det_indices
    ]
    unmatched_tracks = [
        tracks[i] for i in range(n_tracks) if i not in matched_track_ids
    ]

    # ------------------------------------------------------------------
    # 9. Write all DB updates in one commit block
    # ------------------------------------------------------------------
    with postgis_db.get_cursor(commit=True) as cur:
        # --- Matched: update existing tracks ---
        for track, det, cost_val in assignments:
            prev_lon = float(track["lon"])
            prev_lat = float(track["lat"])
            new_lon = float(det["lon"])
            new_lat = float(det["lat"])

            last_seen_aware = track["last_seen"]
            if last_seen_aware.tzinfo is None:
                last_seen_aware = last_seen_aware.replace(tzinfo=timezone.utc)

            dt_seconds = (acq_time - last_seen_aware).total_seconds()
            vel = _velocity_from_observations(
                prev_lon, prev_lat, new_lon, new_lat, dt_seconds
            )

            cur.execute(
                """
                UPDATE detection_tracks SET
                    obs_count   = obs_count + 1,
                    last_seen   = %s,
                    last_centroid = ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    last_velocity = %s,
                    status = CASE
                        WHEN status = 'tentative' AND obs_count + 1 >= 2 THEN 'confirmed'
                        WHEN pinned THEN 'pinned'
                        ELSE status
                    END,
                    miss_count  = 0,
                    updated_at  = NOW()
                WHERE id = %s
                """,
                (
                    acq_time,
                    new_lon, new_lat,
                    json.dumps(vel),
                    track["id"],
                ),
            )

            # Determine seq_index = current obs_count (0-based before this update)
            seq_index = int(track["obs_count"])

            cur.execute(
                """
                INSERT INTO detection_track_members
                    (track_id, detection_id, pass_id, observed_at, centroid, seq_index, cost)
                VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s)
                ON CONFLICT (detection_id) DO NOTHING
                """,
                (
                    track["id"], det["id"], pass_id,
                    acq_time,
                    new_lon, new_lat,
                    seq_index, float(cost_val),
                ),
            )

            # Rebuild path linestring if obs_count will be >= 2
            new_obs_count = seq_index + 1
            if new_obs_count >= 2:
                cur.execute(
                    """
                    UPDATE detection_tracks dt SET
                        path = (
                            SELECT ST_MakeLine(m.centroid ORDER BY m.observed_at)
                            FROM detection_track_members m WHERE m.track_id = dt.id
                        ),
                        updated_at = NOW()
                    WHERE dt.id = %s
                    """,
                    (track["id"],),
                )

            stats["assigned"] += 1

        # --- Unmatched detections: seed new tentative tracks ---
        for di in unmatched_det_indices:
            det = detections[di]
            if float(det.get("confidence", 0.0)) < INIT_CONF_THRESHOLD:
                continue

            track_id = _insert_new_track(cur, det, acq_time, pass_id)
            stats["new_tracks"] += 1

        # --- Unmatched tracks: age / mark coast or lost ---
        for track in unmatched_tracks:
            track_id = track["id"]

            last_seen_t = track["last_seen"]
            if last_seen_t.tzinfo is None:
                last_seen_t = last_seen_t.replace(tzinfo=timezone.utc)

            delta_t = (acq_time - last_seen_t).total_seconds()
            if delta_t < 0:
                delta_t = 0.0

            pred_lat, pred_lon = _predict_position(track, delta_t)

            if footprint_wkb is None:
                is_inside = False  # treat as "outside" → occlusion, don't age miss_count
            else:
                cur.execute(
                    """
                    SELECT ST_Within(
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                        footprint
                    ) AS inside
                    FROM satellite_passes WHERE id = %s
                    """,
                    (pred_lon, pred_lat, pass_id),
                )
                inside_row = cur.fetchone()
                is_inside = bool(inside_row and inside_row["inside"])

            if is_inside and not occluded_by_cloud:
                # Track should have been seen — genuine miss
                pinned = bool(track.get("pinned", False))
                cur.execute(
                    """
                    UPDATE detection_tracks SET
                        miss_count = miss_count + 1,
                        status = CASE
                            WHEN pinned THEN 'pinned'
                            WHEN miss_count + 1 >= %s THEN 'lost'
                            ELSE 'coast'
                        END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (MAX_MISS_COUNT, track_id),
                )
                new_miss = int(track.get("miss_count", 0)) + 1
                stats["missed_tracks"] += 1
                if new_miss >= MAX_MISS_COUNT and not pinned:
                    stats["lost_tracks"] += 1
            # else: outside footprint or cloud occluded — skip (no penalty)

    return stats


# ---------------------------------------------------------------------------
# Reprocess
# ---------------------------------------------------------------------------

def reprocess_all_tracks(*, postgis_db, since: datetime | None = None) -> dict:
    """Wipe detection_tracks/members and replay all passes chronologically.

    since: optional datetime — only replay passes with acquisition_time >= since
    """
    # Wipe tables and reset sequences
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM detection_track_members")
        cur.execute("DELETE FROM detection_tracks")
        cur.execute("ALTER SEQUENCE detection_tracks_id_seq RESTART WITH 1")
        cur.execute("ALTER SEQUENCE detection_track_members_id_seq RESTART WITH 1")

    # Load passes chronologically
    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT id FROM satellite_passes
            WHERE (%s IS NULL OR acquisition_time >= %s)
            ORDER BY acquisition_time ASC
            """,
            (since, since),
        )
        pass_ids = [row["id"] for row in cur.fetchall()]

    aggregate: dict[str, int] = {
        "assigned": 0,
        "new_tracks": 0,
        "missed_tracks": 0,
        "lost_tracks": 0,
    }

    for pid in pass_ids:
        result = update_tracks_for_pass(pid, postgis_db=postgis_db)
        for key in aggregate:
            aggregate[key] += result.get(key, 0)

    aggregate["passes_replayed"] = len(pass_ids)
    return aggregate


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _insert_new_track(cur, det: dict, acq_time: datetime, pass_id: int) -> int:
    """Insert a new tentative track for a detection; returns new track id."""
    det_class = det.get("class", "unknown")
    category = _tracker_category(det_class)
    threat_info = assess_detection_threat(det_class, confidence=det.get("confidence", 0.0))
    threat_level = threat_info.get("threat_level", "low")
    track_uid = str(uuid.uuid4())
    lon = float(det["lon"])
    lat = float(det["lat"])

    cur.execute(
        """
        INSERT INTO detection_tracks
            (track_uid, primary_class, category, threat_level, status,
             obs_count, first_seen, last_seen,
             last_centroid, last_velocity, metadata)
        VALUES (%s, %s, %s, %s, 'tentative', 1, %s, %s,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s)
        RETURNING id
        """,
        (
            track_uid,
            det_class,
            category,
            threat_level,
            acq_time,
            acq_time,
            lon, lat,
            json.dumps({"vx_mps": 0.0, "vy_mps": 0.0}),
            json.dumps({"seeded_by_pass": pass_id}),
        ),
    )
    row = cur.fetchone()
    track_id = row["id"]

    cur.execute(
        """
        INSERT INTO detection_track_members
            (track_id, detection_id, pass_id, observed_at,
             centroid, seq_index, cost)
        VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s)
        ON CONFLICT (detection_id) DO NOTHING
        """,
        (
            track_id, det["id"], pass_id,
            acq_time,
            lon, lat,
            0, 0.0,
        ),
    )
    return track_id


def _create_new_tracks(
    detections: list[dict],
    acq_time: datetime,
    pass_id: int,
    postgis_db,
    stats: dict,
) -> None:
    """Seed tentative tracks for all qualifying detections (no existing tracks)."""
    with postgis_db.get_cursor(commit=True) as cur:
        for det in detections:
            if float(det.get("confidence", 0.0)) < INIT_CONF_THRESHOLD:
                continue
            _insert_new_track(cur, det, acq_time, pass_id)
            stats["new_tracks"] += 1


def _age_unmatched_tracks(
    tracks: list[dict],
    acq_time: datetime,
    occluded_by_cloud: bool,
    pass_row: dict,
    stats: dict,
    postgis_db,
) -> None:
    """Age all tracks when there are no detections in a pass."""
    if not tracks:
        return

    pass_id = pass_row["id"]
    footprint_wkb = pass_row.get("footprint")  # may be None

    with postgis_db.get_cursor(commit=True) as cur:
        for track in tracks:
            last_seen_t = track["last_seen"]
            if last_seen_t.tzinfo is None:
                last_seen_t = last_seen_t.replace(tzinfo=timezone.utc)

            delta_t = (acq_time - last_seen_t).total_seconds()
            if delta_t < 0:
                delta_t = 0.0

            pred_lat, pred_lon = _predict_position(track, delta_t)

            if footprint_wkb is None:
                is_inside = False  # treat as "outside" → occlusion, don't age miss_count
            else:
                cur.execute(
                    """
                    SELECT ST_Within(
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                        footprint
                    ) AS inside
                    FROM satellite_passes WHERE id = %s
                    """,
                    (pred_lon, pred_lat, pass_id),
                )
                inside_row = cur.fetchone()
                is_inside = bool(inside_row and inside_row["inside"])

            if is_inside and not occluded_by_cloud:
                pinned = bool(track.get("pinned", False))
                cur.execute(
                    """
                    UPDATE detection_tracks SET
                        miss_count = miss_count + 1,
                        status = CASE
                            WHEN pinned THEN 'pinned'
                            WHEN miss_count + 1 >= %s THEN 'lost'
                            ELSE 'coast'
                        END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (MAX_MISS_COUNT, track["id"]),
                )
                new_miss = int(track.get("miss_count", 0)) + 1
                stats["missed_tracks"] += 1
                if new_miss >= MAX_MISS_COUNT and not pinned:
                    stats["lost_tracks"] += 1
