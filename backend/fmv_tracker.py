"""
fmv_tracker.py — Post-inference FMV (drone-video) track consolidation.

`process_fmv` slices a clip into overlapping windows and runs one SAM3
`/detect_video` session per (window, prompt). Identity therefore breaks at
every window seam (a fresh tracker each window) and at every prompt (one
session per concept), so a single physical object accumulates dozens of
distinct ``metadata.track_id`` values across a clip and can carry several
conflicting ``class`` labels. The FmvPlayer side panel groups by track_id,
so it shows one row per fragment — the list grows per frame.

This module runs *once after* a clip finishes processing and re-associates
every ``fmv_detections`` row of the clip into stable, clip-global tracks:

  * frame-to-frame association (Hungarian per frame) stitches temporal
    continuity across window seams and brief misses;
  * a track-merge pass collapses co-temporal duplicates (the same object
    seen under two prompts at the same frames — frame-to-frame assignment
    is 1:1 so it can never merge those on its own);
  * one canonical class is voted per track by *temporal support* (frame
    count), not by single-frame peak confidence — a one-frame high-score
    misfire loses to a label that persists;
  * cross-prompt per-(track, frame) duplicate rows are soft-deleted, the
    highest-value survivor kept.

The pass only relabels and de-duplicates *observations* — it never removes
a class from the ontology, and every soft-deleted row keeps its original
class/track_id in metadata and is recoverable. See
``docs/decisions/why-fmv-track-consolidation.md``.

Public API:
    consolidate_fmv_tracks(clip_id, *, postgis_db) -> dict
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from datetime import datetime, timezone
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from geometry import iou_cxcywh

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration — all env-overridable. `FMV_TRACKER_COST_WEIGHTS` is a JSON
# object mirroring `tracker.py`'s `TRACKER_COST_WEIGHTS`, kept separate so
# FMV tuning never perturbs the satellite tracker.
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS = {"iou": 1.0, "emb": 0.6, "gap": 0.3, "class": 0.4}


def _load_weights() -> dict[str, float]:
    weights = dict(_DEFAULT_WEIGHTS)
    raw = (os.getenv("FMV_TRACKER_COST_WEIGHTS") or "").strip()
    if raw:
        try:
            override = json.loads(raw)
            for key in weights:
                if key in override:
                    weights[key] = float(override[key])
        except (ValueError, TypeError):
            logger.warning("FMV_TRACKER_COST_WEIGHTS is not valid JSON; using defaults")
    return weights


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


_WEIGHTS = _load_weights()
# A pair is assignable only if it clears at least one of these gates.
_MIN_IOU = _env_float("FMV_TRACK_MIN_IOU", 0.30)
_MIN_EMB_SIM = _env_float("FMV_TRACK_MIN_EMB_SIM", 0.55)
# Temporal gate, in seconds — converted to source frames using the clip fps.
_MAX_GAP_SECONDS = _env_float("FMV_TRACK_MAX_FRAME_GAP_SECONDS", 2.0)
# Hungarian assignments above this cost are rejected (tracks spawn instead).
_MATCH_THRESHOLD = _env_float("FMV_TRACK_MATCH_THRESHOLD", 1.50)
# Track-merge pass: two tracks merge when this fraction of their shared
# frames have boxes overlapping at >= _MERGE_IOU.
_MERGE_IOU = _env_float("FMV_TRACK_MERGE_IOU", 0.55)
_MERGE_MIN_FRACTION = _env_float("FMV_TRACK_MERGE_MIN_FRACTION", 0.60)
_NEUTRAL_EMB_SIM = 0.5  # used when either side lacks a usable embedding


# ---------------------------------------------------------------------------
# Best-effort helpers around optional backend modules. Imported lazily / with
# fallbacks so the pure consolidation logic stays unit-testable without a DB
# or the ontology tree.
# ---------------------------------------------------------------------------

def _embedding_vector(meta: dict | None) -> np.ndarray | None:
    """Unit-norm embedding from a detection's metadata, or None.

    Delegates to `tracker._embedding_vector` (it already handles the raw
    list and `{fp16_b64, dim}` shapes the inference service emits). Falls
    back to None if `tracker` cannot be imported.
    """
    try:
        from tracker import _embedding_vector as _impl
    except Exception:  # pragma: no cover - tracker is a core backend module
        return None
    return _impl(meta)


def _parent_class(label: str) -> str:
    """Open-vocabulary-safe parent bucket for a class label.

    `ontology.normalize` never raises and falls back to the label itself
    when the ontology tree is unavailable (e.g. in unit tests).
    """
    try:
        from ontology import normalize
        return (normalize(label).parent_class or "").strip().lower() or str(label or "").strip().lower()
    except Exception:
        return str(label or "").strip().lower()


# ---------------------------------------------------------------------------
# Pure consolidation core — operates on plain detection dicts so it can be
# unit-tested without a database. A detection dict has keys:
#   id (int), frame (int), cls (str), conf (float),
#   bbox (list — cxcywh-normalised, [] for heartbeat rows),
#   orig_tid (Any — pre-consolidation metadata.track_id, may be None),
#   emb (np.ndarray | None)
# ---------------------------------------------------------------------------

def _emb_sim(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """Cosine similarity of two unit-norm embeddings clamped to [0, 1].

    Returns a neutral 0.5 when either side is missing or shapes differ, so
    association degrades gracefully to the geometry + gap + class terms.
    """
    if a is None or b is None or a.shape != b.shape:
        return _NEUTRAL_EMB_SIM
    return float(max(0.0, min(1.0, float(np.dot(a, b)))))


def _class_penalty(track_class: str, det_class: str) -> float:
    """Soft class-mismatch penalty — never a hard gate. Merging two
    differently-labelled detections of one object is the goal of this pass,
    so class can only ever break ties, never forbid an assignment."""
    if (track_class or "") == (det_class or ""):
        return 0.0
    if _parent_class(track_class) == _parent_class(det_class):
        return 0.3
    return 0.6


def _pair_cost(track: dict, det: dict, frame: int, max_gap: int) -> float | None:
    """Association cost between an active track and a candidate detection.

    Returns None when the pair fails a hard gate (too far in time, or
    neither geometrically nor visually similar enough)."""
    delta_f = frame - track["last_frame"]
    if delta_f > max_gap:
        return None
    iou = iou_cxcywh(track["last_bbox"], det["bbox"])
    emb_sim = _emb_sim(track["last_emb"], det["emb"])
    if iou < _MIN_IOU and emb_sim < _MIN_EMB_SIM:
        return None
    penalty = _class_penalty(track["last_class"], det["cls"])
    return (
        _WEIGHTS["iou"] * (1.0 - iou)
        + _WEIGHTS["emb"] * (1.0 - emb_sim)
        + _WEIGHTS["gap"] * (delta_f / max_gap if max_gap else 0.0)
        + _WEIGHTS["class"] * penalty
    )


def _new_track(tid: int, det: dict) -> dict:
    return {
        "tid": tid,
        "last_frame": det["frame"],
        "last_bbox": det["bbox"],
        "last_emb": det["emb"],
        "last_class": det["cls"],
        "members": [det],
        "by_frame": {det["frame"]: [det]},
    }


def _attach(track: dict, det: dict) -> None:
    track["last_frame"] = det["frame"]
    track["last_bbox"] = det["bbox"]
    if det["emb"] is not None:
        track["last_emb"] = det["emb"]
    track["last_class"] = det["cls"]
    track["members"].append(det)
    track["by_frame"].setdefault(det["frame"], []).append(det)


def _associate_spatial(spatial: list[dict], max_gap: int) -> list[dict]:
    """Frame-to-frame association: greedy over frames, Hungarian within
    each frame. Returns the list of preliminary tracks."""
    frames = sorted({d["frame"] for d in spatial})
    by_frame: dict[int, list[dict]] = {}
    for d in spatial:
        by_frame.setdefault(d["frame"], []).append(d)

    prelim: list[dict] = []
    active: list[dict] = []
    next_tid = 0
    for frame in frames:
        # Retire tracks idle longer than the temporal gate.
        active = [t for t in active if frame - t["last_frame"] <= max_gap]
        dets = by_frame[frame]
        unmatched = dets
        if active:
            cost = np.full((len(active), len(dets)), 1e9, dtype=float)
            for i, t in enumerate(active):
                for j, d in enumerate(dets):
                    c = _pair_cost(t, d, frame, max_gap)
                    if c is not None:
                        cost[i, j] = c
            rows_idx, cols_idx = linear_sum_assignment(cost)
            matched_cols: set[int] = set()
            for i, j in zip(rows_idx, cols_idx):
                if cost[i, j] < _MATCH_THRESHOLD:
                    _attach(active[i], dets[j])
                    matched_cols.add(j)
            unmatched = [d for j, d in enumerate(dets) if j not in matched_cols]
        for d in unmatched:
            t = _new_track(next_tid, d)
            next_tid += 1
            prelim.append(t)
            active.append(t)
    return prelim


def _merge_cotemporal(prelim: list[dict]) -> dict[int, int]:
    """Union-find merge of preliminary tracks that are co-temporal *and*
    spatially coincident — i.e. the same object tracked twice (typically
    one session per prompt). Returns a map prelim-tid -> root tid."""
    parent = {t["tid"]: t["tid"] for t in prelim}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_id = {t["tid"]: t for t in prelim}
    frame_tracks: dict[int, list[int]] = {}
    for t in prelim:
        for f in t["by_frame"]:
            frame_tracks.setdefault(f, []).append(t["tid"])

    pairs: set[tuple[int, int]] = set()
    for tids in frame_tracks.values():
        for i in range(len(tids)):
            for j in range(i + 1, len(tids)):
                pairs.add((min(tids[i], tids[j]), max(tids[i], tids[j])))

    for a, b in sorted(pairs):
        if find(a) == find(b):
            continue
        ta, tb = by_id[a], by_id[b]
        shared = set(ta["by_frame"]) & set(tb["by_frame"])
        if not shared:
            continue
        good = 0
        for f in shared:
            best = max(
                iou_cxcywh(da["bbox"], db["bbox"])
                for da in ta["by_frame"][f]
                for db in tb["by_frame"][f]
            )
            if best >= _MERGE_IOU:
                good += 1
        if good / len(shared) >= _MERGE_MIN_FRACTION:
            union(a, b)

    return {tid: find(tid) for tid in parent}


def _vote_class(dets: list[dict]) -> str:
    """Canonical class for a track: the label with the most *distinct
    frames* of support (temporal persistence beats a single high-confidence
    misfire); ties broken by median confidence, then lexically."""
    frames_by_class: dict[str, set[int]] = {}
    conf_by_class: dict[str, list[float]] = {}
    for d in dets:
        cls = d["cls"] or ""
        frames_by_class.setdefault(cls, set()).add(d["frame"])
        conf_by_class.setdefault(cls, []).append(d["conf"])
    if not frames_by_class:
        return ""
    return sorted(
        frames_by_class,
        key=lambda c: (len(frames_by_class[c]), statistics.median(conf_by_class[c]), c),
    )[-1]


def consolidate(detections: list[dict], *, max_gap_frames: int) -> dict[str, Any]:
    """Pure consolidation core. Given the clip's detection dicts, return a
    plan: a stable consolidated id + voted class per surviving row, and the
    set of cross-prompt duplicate rows to soft-delete.

    Result keys:
      tracks            — list of {consolidated_id, canonical_class, frame_min}
      assignment        — {det_id: (consolidated_id, canonical_class)} for survivors
      soft_delete_ids   — list of det ids (per-frame duplicates to retire)
      heartbeat_rows    — count of heartbeat (empty-bbox) rows
    """
    spatial = [d for d in detections if d["bbox"]]
    heartbeat = [d for d in detections if not d["bbox"]]

    prelim = _associate_spatial(spatial, max_gap_frames)
    root_of = _merge_cotemporal(prelim)

    # Group preliminary tracks into final consolidated tracks.
    groups: dict[int, list[dict]] = {}  # root tid -> spatial detections
    for t in prelim:
        groups.setdefault(root_of[t["tid"]], []).extend(t["members"])

    # Map each pre-consolidation track_id to the consolidated group that
    # owns most of its spatial rows — used to route heartbeat rows.
    orig_to_group: dict[Any, dict[int, int]] = {}
    for root, dets in groups.items():
        for d in dets:
            tid = d["orig_tid"]
            if tid is None:
                continue
            orig_to_group.setdefault(tid, {})[root] = orig_to_group.setdefault(tid, {}).get(root, 0) + 1
    orig_best_group = {
        tid: max(counts.items(), key=lambda kv: kv[1])[0]
        for tid, counts in orig_to_group.items()
    }

    # Attach heartbeat rows: to an existing group when their lineage is
    # known, otherwise as their own degenerate (heartbeat-only) track.
    heartbeat_extra: dict[int, list[dict]] = {}  # root -> heartbeat dets
    next_synthetic = (max(groups) + 1) if groups else 0
    hb_by_tid: dict[Any, list[dict]] = {}
    for d in heartbeat:
        hb_by_tid.setdefault(d["orig_tid"], []).append(d)
    for tid, dets in hb_by_tid.items():
        if tid is not None and tid in orig_best_group:
            heartbeat_extra.setdefault(orig_best_group[tid], []).extend(dets)
        else:
            groups[next_synthetic] = []  # heartbeat-only track
            heartbeat_extra[next_synthetic] = dets
            next_synthetic += 1

    # Deterministic consolidated ids: order tracks by their earliest member
    # frame, then earliest member id, so re-runs reproduce the numbering.
    def _track_sort_key(root: int) -> tuple[int, int]:
        members = groups[root] + heartbeat_extra.get(root, [])
        return (min(d["frame"] for d in members), min(d["id"] for d in members))

    ordered_roots = sorted(groups, key=_track_sort_key)

    tracks_out: list[dict] = []
    assignment: dict[int, tuple[int, str]] = {}
    soft_delete_ids: list[int] = []
    for idx, root in enumerate(ordered_roots):
        consolidated_id = idx + 1  # 1-based: track_id 0 is falsy in the UI
        spatial_dets = groups[root]
        hb_dets = heartbeat_extra.get(root, [])
        # Vote class on spatial rows; fall back to heartbeat labels for a
        # heartbeat-only track (they still carry the session class).
        canonical = _vote_class(spatial_dets) or _vote_class(hb_dets)
        tracks_out.append({
            "consolidated_id": consolidated_id,
            "canonical_class": canonical,
            "frame_min": _track_sort_key(root)[0],
        })
        # Per-(track, frame) duplicate collapse on spatial rows: keep one
        # row per frame — prefer the canonical class, then top confidence.
        by_frame: dict[int, list[dict]] = {}
        for d in spatial_dets:
            by_frame.setdefault(d["frame"], []).append(d)
        for frame_dets in by_frame.values():
            if len(frame_dets) > 1:
                frame_dets.sort(key=lambda d: (d["cls"] == canonical, d["conf"]), reverse=True)
                for loser in frame_dets[1:]:
                    soft_delete_ids.append(loser["id"])
            assignment[frame_dets[0]["id"]] = (consolidated_id, canonical)
        for d in hb_dets:
            assignment[d["id"]] = (consolidated_id, canonical)

    return {
        "tracks": tracks_out,
        "assignment": assignment,
        "soft_delete_ids": soft_delete_ids,
        "heartbeat_rows": len(heartbeat),
    }


# ---------------------------------------------------------------------------
# Database entry point
# ---------------------------------------------------------------------------

def _row_detection(row: dict) -> dict:
    """Build a consolidation detection dict from an fmv_detections row."""
    bbox_raw = row.get("bbox")
    bbox: list[float] = []
    if isinstance(bbox_raw, (list, tuple)) and len(bbox_raw) == 4:
        try:
            cand = [float(v) for v in bbox_raw]
            if cand[2] > 0 and cand[3] > 0:
                bbox = cand
        except (TypeError, ValueError):
            bbox = []
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "id": int(row["id"]),
        "frame": int(row["frame_index"]),
        "cls": row.get("class") or "",
        "conf": float(row.get("confidence") or 0.0),
        "bbox": bbox,
        "orig_tid": meta.get("track_id"),
        "emb": _embedding_vector(meta),
        "meta": meta,
    }


def consolidate_fmv_tracks(clip_id: int, *, postgis_db) -> dict:
    """Consolidate every live ``fmv_detections`` row of ``clip_id`` into
    stable, clip-global tracks with one canonical class each.

    Idempotent: re-running reads only ``deleted_at IS NULL`` rows, so the
    second run finds nothing to collapse and reproduces the same ids.

    Returns a stats dict:
      {clip_id, input_rows, consolidated_tracks, rows_soft_deleted,
       rows_rewritten, heartbeat_rows, class_changes}
    """
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            SELECT id, frame_index, class, confidence, bbox, metadata
            FROM fmv_detections
            WHERE clip_id = %s AND deleted_at IS NULL
            ORDER BY frame_index ASC, id ASC
            """,
            (clip_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return {"clip_id": clip_id, "input_rows": 0, "consolidated_tracks": 0,
                    "rows_soft_deleted": 0, "rows_rewritten": 0,
                    "heartbeat_rows": 0, "class_changes": 0}

        cur.execute("SELECT fps FROM fmv_clips WHERE id = %s", (clip_id,))
        fps_row = cur.fetchone()
        fps = float((fps_row["fps"] if fps_row else None) or 30.0) or 30.0
        max_gap_frames = max(1, int(round(fps * _MAX_GAP_SECONDS)))

        detections = [_row_detection(r) for r in rows]
        by_id = {d["id"]: d for d in detections}
        plan = consolidate(detections, max_gap_frames=max_gap_frames)

        now_iso = datetime.now(timezone.utc).isoformat()
        soft_delete_ids = set(plan["soft_delete_ids"])
        class_changes = 0
        rewrites = 0
        for det_id, (consolidated_id, canonical) in plan["assignment"].items():
            if det_id in soft_delete_ids:
                continue
            det = by_id[det_id]
            meta = dict(det["meta"])
            # Preserve the *true* originals across re-runs (COALESCE semantics).
            if "original_track_id" not in meta:
                meta["original_track_id"] = meta.get("track_id")
            if "original_class" not in meta:
                meta["original_class"] = det["cls"]
            meta["track_id"] = consolidated_id
            meta["consolidated"] = True
            meta["consolidation_run_at"] = now_iso
            if canonical != det["cls"]:
                class_changes += 1
            cur.execute(
                "UPDATE fmv_detections SET class = %s, metadata = %s::jsonb WHERE id = %s",
                (canonical, json.dumps(meta), det_id),
            )
            rewrites += 1

        for det_id in soft_delete_ids:
            cur.execute(
                "UPDATE fmv_detections SET deleted_at = NOW() WHERE id = %s AND deleted_at IS NULL",
                (det_id,),
            )

    result = {
        "clip_id": clip_id,
        "input_rows": len(rows),
        "consolidated_tracks": len(plan["tracks"]),
        "rows_soft_deleted": len(soft_delete_ids),
        "rows_rewritten": rewrites,
        "heartbeat_rows": plan["heartbeat_rows"],
        "class_changes": class_changes,
    }
    logger.info("FMV consolidation clip=%s: %s", clip_id, result)
    return result
