"""
tracker.py — Hungarian-assignment multi-pass detection tracker for satellite OSINT.

Public API:
    update_tracks_for_pass(pass_id, *, postgis_db) -> dict
    reprocess_all_tracks(*, postgis_db, since=None) -> dict
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from math import atan2, degrees, radians, sin, cos, sqrt

import numpy as np
from pyproj import Geod
from scipy.optimize import linear_sum_assignment

from threat_assessment import category_for_class, assess_detection_threat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — Phase 4.17 (per-state V_MAX) + Phase 4.19 (configurable weights)
# ---------------------------------------------------------------------------

# Phase 4.17: V_MAX now keyed by (category, state) instead of category alone.
# Aircraft taxi speed (14 m/s) is correct for the "ground" state but absurdly
# slow for an airborne aircraft (which can do 250+ m/s). Ground vehicles on
# a highway exceed the 22 m/s cap. Per-state limits remove the gate that
# silently rejects every airborne or highway update.
V_MAX_PER_STATE: dict[str, dict[str, float]] = {
    "maritime": {
        "default": 16.0,    # ~30 kt typical merchant
        "underway": 25.0,   # ~50 kt fast warship / patrol craft
        "stationary": 1.0,
    },
    "ground": {
        "default": 22.0,    # ~80 km/h urban
        "highway": 40.0,    # ~144 km/h motorway / convoy at speed
        "stationary": 1.0,
    },
    "air": {
        "default": 14.0,    # on-ground aircraft taxi
        "ground": 14.0,
        "airborne": 300.0,  # commercial jet ~M0.85 at altitude
        "stationary": 1.0,
    },
    "infrastructure": {
        "default": 0.0,     # pinned; never moves
    },
    "default": {
        "default": 16.0,
    },
}

# Legacy ``V_MAX[category] = scalar`` view, preserved so any external caller
# / test that reads V_MAX directly still gets the per-category default. New
# code should use ``_v_max(category, state)`` below.
V_MAX: dict[str, float] = {
    cat: states.get("default", states[next(iter(states))])
    for cat, states in V_MAX_PER_STATE.items()
}

MATCH_THRESHOLD = 1.5
INIT_CONF_THRESHOLD = 0.4
MAX_TRACK_AGE_DAYS = 14
MAX_MISS_COUNT = 3
CLOUD_COVER_OCCLUSION = 0.7


def _load_tracker_weights() -> dict[str, float]:
    """Phase 4.19: per-deployment overrides for the Hungarian cost weights.

    Default tuple ``(1.0, 0.6, 0.2)`` is unchanged. Operators tuning the
    tracker for a specific scenario (e.g. low-confidence FMV detections
    in dense urban) can override via ``TRACKER_COST_WEIGHTS`` env JSON::

        {"alpha": 0.8, "beta": 1.0, "gamma": 0.4}

    Or via the ``inference_config`` DB row if/when an admin UI hooks
    that path. Both sources fall back to the defaults below; this keeps
    legacy callers working without any change.
    """
    defaults = {"alpha": 1.0, "beta": 0.6, "gamma": 0.2}
    raw_env = (os.getenv("TRACKER_COST_WEIGHTS") or "").strip()
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                for key in defaults:
                    if key in parsed:
                        try:
                            defaults[key] = max(0.0, float(parsed[key]))
                        except (TypeError, ValueError):
                            continue
        except json.JSONDecodeError:
            logger.warning("TRACKER_COST_WEIGHTS is not valid JSON; ignoring")
    return defaults


_TRACKER_WEIGHTS = _load_tracker_weights()
ALPHA = _TRACKER_WEIGHTS["alpha"]   # spatial distance weight
BETA  = _TRACKER_WEIGHTS["beta"]    # class penalty weight
GAMMA = _TRACKER_WEIGHTS["gamma"]   # confidence penalty weight
# Phase 4.18: re-ID via DINOv3-SAT embedding cosine similarity. Default 0.0
# (disabled) preserves legacy behaviour for callers that don't carry embeddings.
# When set > 0, the Hungarian cost incorporates (1 - cos_sim) so a detection
# whose visual embedding matches an existing track's anchor embedding is
# preferred over one with similar geometry but different appearance.
DELTA = max(0.0, float(os.getenv("TRACKER_EMBEDDING_WEIGHT", "0.0") or "0.0"))


def _embedding_vector(item: dict | None) -> np.ndarray | None:
    """Best-effort extraction of a unit-norm embedding from a track or
    detection dict. Accepts either a raw list/array under ``embedding`` /
    ``embedding_vector`` or the structured ``{"fp16_b64": ..., "dim": ...}``
    shape the inference service emits. Returns ``None`` when no usable
    embedding is found — callers should fall back to the geometry+class cost.
    """
    if not item:
        return None
    embedding = item.get("embedding") or item.get("embedding_vector")
    arr: np.ndarray | None = None
    if isinstance(embedding, dict):
        b64 = embedding.get("fp16_b64") or embedding.get("b64")
        if b64:
            try:
                import base64
                raw = base64.b64decode(b64)
                arr = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
            except Exception:
                arr = None
    elif isinstance(embedding, (list, tuple)):
        try:
            arr = np.asarray(embedding, dtype=np.float32)
        except (TypeError, ValueError):
            arr = None
    elif isinstance(embedding, np.ndarray):
        arr = embedding.astype(np.float32)
    if arr is None or arr.size == 0 or not np.all(np.isfinite(arr)):
        return None
    norm = float(np.linalg.norm(arr))
    if norm <= 0.0:
        return None
    return arr / norm


def _embedding_cost(track: dict, det: dict) -> float:
    """Return ``1 - cos_sim`` in ``[0, 2]`` (0 = identical, 2 = opposite).

    Returns ``0.0`` when either side lacks a usable embedding so the cost
    function degrades gracefully to the geometry+class score.
    """
    if DELTA <= 0.0:
        return 0.0
    t_vec = _embedding_vector(track)
    d_vec = _embedding_vector(det)
    if t_vec is None or d_vec is None:
        return 0.0
    if t_vec.shape != d_vec.shape:
        # Dimension mismatch — different embedding heads. Bail to identity
        # so the rest of the cost still applies.
        return 0.0
    sim = float(np.dot(t_vec, d_vec))
    return max(0.0, 1.0 - sim)

_GEOD = Geod(ellps="WGS84")


# ---------------------------------------------------------------------------
# Phase 4.16 — light Kalman state: process-noise scalars (m/s²) per
# (category, state). These are the σ_a values driving the constant-velocity
# CA→CV process model (position growth ~= σ_a · dt² / 2). Manoeuvring
# aircraft have the highest acceleration potential; pinned infrastructure
# is effectively zero. The "ground/highway" entry covers convoys at speed
# changing direction at junctions.
# ---------------------------------------------------------------------------

KALMAN_PROCESS_NOISE: dict[str, dict[str, float]] = {
    "air":            {"default": 5.0, "ground": 1.5, "airborne": 10.0, "stationary": 0.1},
    "ground":         {"default": 2.0, "highway": 3.0, "stationary": 0.05},
    "maritime":       {"default": 1.0, "underway": 2.0, "stationary": 0.05},
    "infrastructure": {"default": 0.0},
    "default":        {"default": 2.0},
}

# Observation-noise σ in metres — driven by the GSD-derived position
# uncertainty in worker.py. When the detection carries
# ``position_uncertainty_m``, we use it directly; otherwise this floor
# applies. Tracks initialised from a single observation start at this σ.
KALMAN_OBSERVATION_NOISE_FLOOR_M = max(0.5, float(os.getenv("KALMAN_OBS_NOISE_FLOOR_M", "5.0") or "5.0"))

# Multiplier on the predicted 1-σ gate when computing the per-track
# assignment cutoff. 3σ ≈ 99.7% under Gaussian assumptions; raise this if
# the operator wants more permissive gating.
KALMAN_GATE_SIGMAS = max(1.0, float(os.getenv("KALMAN_GATE_SIGMAS", "3.0") or "3.0"))


def _kalman_process_sigma_a(category: str, state: str | None) -> float:
    """Return process-noise σ_a (m/s²) for the given (category, state)."""
    table = KALMAN_PROCESS_NOISE.get(category) or KALMAN_PROCESS_NOISE["default"]
    if state and state in table:
        return table[state]
    return table.get("default", KALMAN_PROCESS_NOISE["default"]["default"])


def _predicted_position_sigma_m(track: dict, delta_t_seconds: float, category: str, state: str | None) -> float:
    """Phase 4.16: predicted 1-σ positional uncertainty in metres after dt.

    Constant-velocity Kalman propagation gives::

        σ_x(t+dt)² = σ_x(t)²  +  (σ_v · dt)²  +  (σ_a · dt² / 2)²

    The σ_v term collapses to 0 when the track has no observed velocity
    (cold start), and σ_a is the per-(category, state) process noise. The
    σ_x(t) base is taken from the track's last stored ``position_sigma_m``
    or falls back to the observation-noise floor.
    """
    try:
        sigma_x = float(track.get("position_sigma_m") or KALMAN_OBSERVATION_NOISE_FLOOR_M)
    except (TypeError, ValueError):
        sigma_x = KALMAN_OBSERVATION_NOISE_FLOOR_M
    try:
        sigma_v = float(track.get("velocity_sigma_mps") or 0.0)
    except (TypeError, ValueError):
        sigma_v = 0.0
    sigma_a = _kalman_process_sigma_a(category, state)
    sigma_pred_sq = (
        sigma_x ** 2
        + (sigma_v * delta_t_seconds) ** 2
        + (0.5 * sigma_a * delta_t_seconds ** 2) ** 2
    )
    return sqrt(max(0.0, sigma_pred_sq))


def _kalman_update_sigma(track: dict, observation_sigma_m: float) -> float:
    """1-D scalar Kalman update on positional σ.

    σ_post² = (σ_prior² · σ_obs²) / (σ_prior² + σ_obs²)

    Returns the posterior σ. Caller should also write the posterior σ_v
    via ``_velocity_sigma_after_update`` if a velocity was just observed.
    """
    sigma_prior = max(0.01, float(track.get("position_sigma_m") or KALMAN_OBSERVATION_NOISE_FLOOR_M))
    sigma_obs = max(0.01, float(observation_sigma_m))
    denom = sigma_prior ** 2 + sigma_obs ** 2
    if denom <= 0.0:
        return sigma_prior
    sigma_post_sq = (sigma_prior ** 2 * sigma_obs ** 2) / denom
    return sqrt(max(0.0, sigma_post_sq))


def _velocity_sigma_after_update(track: dict, dt_seconds: float, observation_sigma_m: float) -> float:
    """Posterior 1-σ on velocity given a position observation with σ_obs at
    elapsed time ``dt_seconds`` since the last observation. Standard CV
    Kalman closed-form. Returns σ_v in m/s.
    """
    if dt_seconds <= 0.0:
        try:
            return max(0.0, float(track.get("velocity_sigma_mps") or 0.0))
        except (TypeError, ValueError):
            return 0.0
    sigma_pos = max(0.01, float(observation_sigma_m))
    return sigma_pos / dt_seconds


# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------

def _tracker_category(class_name: str) -> str:
    """Map a detection class to one of the V_MAX category keys."""
    raw = category_for_class(class_name)  # may return "combat", "unknown", etc.
    if raw in V_MAX:
        return raw
    return "default"


def _v_max(category: str, state: str | None = None) -> float:
    """Phase 4.17: return the maximum velocity for ``(category, state)``.

    ``state`` is one of ``stationary | ground | airborne | underway |
    highway | default`` — meaning is category-specific. When state is
    None or unknown, falls back to the category's ``"default"``. When
    the category is unknown, falls back to the global default.
    """
    table = V_MAX_PER_STATE.get(category) or V_MAX_PER_STATE["default"]
    if state and state in table:
        return table[state]
    return table.get("default", V_MAX_PER_STATE["default"]["default"])


def _track_state(track: dict, category: str) -> str:
    """Infer the kinematic state of a track for V_MAX lookup.

    Uses an explicit ``state`` field if the upstream pipeline set one;
    otherwise derives from the track's last observed velocity:
      * speed < 0.5 m/s → "stationary"
      * category=air and speed > 20 m/s → "airborne"
      * category=ground and speed > 25 m/s → "highway"
      * category=maritime and speed > 18 m/s → "underway"
      * else "default"
    """
    explicit = track.get("state") or track.get("kinematic_state")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    vel = track.get("last_velocity") or {}
    try:
        speed = float(vel.get("speed_mps") or vel.get("speed") or 0.0)
    except (TypeError, ValueError):
        speed = 0.0
    if speed < 0.5:
        return "stationary"
    if category == "air" and speed > 20.0:
        return "airborne"
    if category == "ground" and speed > 25.0:
        return "highway"
    if category == "maritime" and speed > 18.0:
        return "underway"
    return "default"


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
    """Return cost for assigning detection to track, or np.inf if outside gate.

    Phase 4.17: V_MAX is per-(category, state) so an airborne aircraft track
    isn't rejected at the 14 m/s taxi gate and a highway-state ground
    vehicle isn't rejected at the 22 m/s urban gate.

    Phase 4.16: gate widens with predicted state uncertainty (Kalman σ_pred)
    so high-uncertainty tracks (newborn, manoeuvring, long Δt) accept
    farther-out detections while well-localised tracks stay tight. The gate
    is now ``max(V_MAX-based ring, KALMAN_GATE_SIGMAS · σ_pred)`` — old
    V_MAX gate kept as a lower bound to retain legacy semantics on tracks
    without stored uncertainty.
    """
    category = track.get("category") or "default"
    state = _track_state(track, category)
    vm = _v_max(category, state)
    r_gate_vmax = vm * delta_t_seconds * 1.25
    # Phase 4.16: Kalman-predicted positional uncertainty after dt seconds.
    sigma_pred_m = _predicted_position_sigma_m(track, delta_t_seconds, category, state)
    r_gate_kalman = KALMAN_GATE_SIGMAS * sigma_pred_m
    r_gate = max(r_gate_vmax, r_gate_kalman)
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

    # Phase 4.18: re-ID term via embedding cosine. Adds a tie-breaker that
    # disambiguates two same-class detections at similar distances by
    # appearance. No-op when DELTA == 0 or either side lacks an embedding.
    embedding_cost = _embedding_cost(track, det) if DELTA > 0.0 else 0.0

    return ALPHA * d_norm + BETA * class_penalty + GAMMA * conf_penalty + DELTA * embedding_cost


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
