"""Telemetry extraction from FMV (Full Motion Video) containers.

Supports three real sources in priority order, with a synthetic fixture
fallback so the rest of the pipeline never sees empty rows:

1. MISB ST 0601 KLV  -- demuxed from MPEG-2 TS / MP4 `data` streams via
   ffmpeg, parsed with `klvdata`.
2. MP4 GPMD          -- GoPro / DJI metadata track parsed inline (no extra
   dependency; the format is a simple nested KLV).
3. SRT sidecar       -- DJI / Autel subtitle file with bracketed
   key:value telemetry per timestamp window.
4. Fixture           -- sine-wave around Dubai (kept for offline demos).

Output rows match the existing `fmv_frames` insert shape:
    (clip_id, frame_index, timestamp_seconds, telemetry_json, footprint_wkt)
"""

from __future__ import annotations

import json
import logging
import math
import re
import struct
import subprocess
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# Subset of MISB 0601 UDS keys we care about for the map view.
MISB_KEY_MAP = {
    2: "unix_timestamp_us",
    5: "platform_heading",
    6: "platform_pitch",
    7: "platform_roll",
    13: "platform_latitude",
    14: "platform_longitude",
    15: "platform_altitude_msl",
    16: "sensor_horizontal_fov",
    17: "sensor_vertical_fov",
    18: "sensor_azimuth",
    19: "sensor_elevation",
    21: "slant_range",
    22: "target_width",
    23: "frame_center_latitude",
    24: "frame_center_longitude",
    25: "frame_center_elevation",
}


def extract_telemetry(
    video_path: Path,
    clip_id: int,
    duration_s: float,
    fps: Optional[float],
    sidecar_srt: Optional[Path] = None,
) -> list[tuple]:
    """Return rows ready for `INSERT INTO fmv_frames`.

    Falls through extractors until one yields samples; if all fail,
    returns the synthetic sine-wave fixture so the schema is always
    populated.
    """
    fps_value = fps or 30.0

    for extractor, label in (
        (_extract_klv, "misb-klv"),
        (_extract_gpmd, "gpmd"),
        (lambda p: _extract_srt(sidecar_srt) if sidecar_srt else [], "srt"),
    ):
        try:
            samples = extractor(video_path)
        except Exception as exc:
            logger.warning("FMV telemetry extractor %s failed: %s", label, exc)
            samples = []
        if samples:
            logger.info("FMV telemetry: %d samples from %s for clip %s", len(samples), label, clip_id)
            return _samples_to_rows(samples, clip_id, fps_value, source=label)

    logger.info("FMV telemetry: falling back to fixture for clip %s", clip_id)
    return _fixture_rows(clip_id, duration_s, fps_value)


# ---------------------------------------------------------------------------
# KLV (MISB 0601)
# ---------------------------------------------------------------------------

def _extract_klv(video_path: Path) -> list[dict]:
    """Demux KLV data stream via ffmpeg, parse with klvdata.

    Returns a list of `{timestamp_seconds, **fields}` dicts. Empty if the
    container has no KLV track or klvdata is not installed.
    """
    try:
        from klvdata.misb0601 import UASLocalMetadataSet  # type: ignore
    except Exception:
        logger.debug("klvdata not available; skipping KLV extraction")
        return []

    # Find the data stream index that looks like KLV.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(video_path)],
        check=False, text=True, capture_output=True,
    )
    if probe.returncode != 0:
        return []
    streams = json.loads(probe.stdout or "{}").get("streams", [])
    klv_stream = next(
        (s for s in streams
         if s.get("codec_type") == "data"
         and (s.get("codec_tag_string", "").lower() in {"klva", "klv", "smpte"}
              or (s.get("codec_name") or "").lower() in {"klv", "data"})),
        None,
    )
    if not klv_stream:
        return []

    stream_index = klv_stream["index"]
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video_path),
         "-map", f"0:{stream_index}", "-c", "copy", "-f", "data", "-"],
        check=False, capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return []

    samples: list[dict] = []
    try:
        for packet in UASLocalMetadataSet.parsers(proc.stdout):
            row: dict = {}
            for element in packet:
                key = getattr(element, "key", None)
                if key is None:
                    continue
                tag_byte = key[-1] if isinstance(key, (bytes, bytearray)) else key
                name = MISB_KEY_MAP.get(int(tag_byte))
                if not name:
                    continue
                try:
                    row[name] = float(element.value)
                except (TypeError, ValueError):
                    row[name] = element.value
            ts_us = row.pop("unix_timestamp_us", None)
            if ts_us is not None:
                row["unix_timestamp"] = float(ts_us) / 1e6
            samples.append(row)
    except Exception as exc:
        logger.warning("klvdata parse failure: %s", exc)
        return []

    # Resolve relative timestamps: prefer unix_timestamp deltas, else
    # spread evenly across the stream.
    if samples and "unix_timestamp" in samples[0]:
        t0 = samples[0]["unix_timestamp"]
        for sample in samples:
            sample["timestamp_seconds"] = round(sample.get("unix_timestamp", t0) - t0, 3)
    else:
        n = max(1, len(samples))
        for idx, sample in enumerate(samples):
            sample["timestamp_seconds"] = round(idx / n, 3)
    return samples


# ---------------------------------------------------------------------------
# GPMD (GoPro / DJI MP4 metadata track)
# ---------------------------------------------------------------------------

# GPMD is nested 8-byte headers: 4-byte FourCC, 1-byte type, 1-byte size,
# 2-byte big-endian count. Only a handful of FourCC keys matter for FMV.
_GPMD_FOURCCS = {
    "GPS5": ("lat", "lon", "alt", "speed_2d", "speed_3d"),
    "GPSF": ("gps_fix",),
    "GPSU": ("gps_time",),
    "ACCL": ("ax", "ay", "az"),
    "GYRO": ("gx", "gy", "gz"),
}


def _extract_gpmd(video_path: Path) -> list[dict]:
    """Find a gpmd-tagged stream and parse the raw KLV-ish payload."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(video_path)],
        check=False, text=True, capture_output=True,
    )
    if probe.returncode != 0:
        return []
    streams = json.loads(probe.stdout or "{}").get("streams", [])
    gpmd_stream = next(
        (s for s in streams
         if s.get("codec_type") == "data"
         and (s.get("codec_tag_string", "").lower() == "gpmd"
              or "gopro" in (s.get("tags", {}).get("handler_name", "").lower()))),
        None,
    )
    if not gpmd_stream:
        return []

    stream_index = gpmd_stream["index"]
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video_path),
         "-map", f"0:{stream_index}", "-c", "copy", "-f", "data", "-"],
        check=False, capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return []

    raw = proc.stdout
    samples: list[dict] = []
    scale = (1.0, 1.0, 1.0, 1.0, 1.0)
    offset = 0
    current: dict = {}

    while offset + 8 <= len(raw):
        fourcc = raw[offset:offset + 4].decode("ascii", errors="ignore")
        type_byte = raw[offset + 4:offset + 5]
        size = raw[offset + 5]
        count = struct.unpack(">H", raw[offset + 6:offset + 8])[0]
        offset += 8
        payload_len = size * count
        # GPMD payloads are 4-byte aligned.
        padded_len = (payload_len + 3) & ~0x03
        payload = raw[offset:offset + payload_len]
        offset += padded_len

        if not fourcc.strip("\x00 "):
            continue

        if fourcc == "SCAL" and type_byte == b"l":
            try:
                scale_values = struct.unpack(f">{count}l", payload)
                scale = tuple(v if v != 0 else 1.0 for v in scale_values)
            except struct.error:
                pass
        elif fourcc == "GPS5" and type_byte == b"l" and size == 20:
            try:
                values = struct.iter_unpack(">5l", payload)
                first = next(values, None)
                if first:
                    lat = first[0] / scale[0]
                    lon = first[1] / scale[1]
                    alt = first[2] / scale[2] if len(scale) > 2 else None
                    current = {
                        "platform_latitude": lat,
                        "platform_longitude": lon,
                        "platform_altitude_msl": alt,
                        "frame_center_latitude": lat,
                        "frame_center_longitude": lon,
                    }
                    samples.append(current)
            except struct.error:
                pass

    if not samples:
        return []
    n = len(samples)
    for idx, sample in enumerate(samples):
        sample["timestamp_seconds"] = round(idx / max(n - 1, 1), 3)
    return samples


# ---------------------------------------------------------------------------
# SRT sidecar
# ---------------------------------------------------------------------------

_SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
_SRT_KV_RE = re.compile(r"\[?\s*([a-z_]+)\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*\]?", re.IGNORECASE)
_SRT_ALIASES = {
    "latitude": "frame_center_latitude",
    "lat": "frame_center_latitude",
    "longitude": "frame_center_longitude",
    "long": "frame_center_longitude",
    "lon": "frame_center_longitude",
    "rel_alt": "platform_altitude_relative",
    "abs_alt": "platform_altitude_msl",
    "altitude": "platform_altitude_msl",
}


def _extract_srt(srt_path: Optional[Path]) -> list[dict]:
    if not srt_path or not srt_path.exists():
        return []
    text = srt_path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n\s*\n", text)
    samples: list[dict] = []
    for block in blocks:
        time_match = _SRT_TIME_RE.search(block)
        if not time_match:
            continue
        h1, m1, s1, ms1 = (int(time_match.group(i)) for i in (1, 2, 3, 4))
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        row: dict = {"timestamp_seconds": round(start, 3)}
        for key, val in _SRT_KV_RE.findall(block):
            mapped = _SRT_ALIASES.get(key.lower())
            if not mapped:
                continue
            try:
                row[mapped] = float(val)
            except ValueError:
                continue
        # Drop frames with no usable location.
        if "frame_center_latitude" in row and "frame_center_longitude" in row:
            row.setdefault("platform_latitude", row["frame_center_latitude"])
            row.setdefault("platform_longitude", row["frame_center_longitude"])
            samples.append(row)
    return samples


# ---------------------------------------------------------------------------
# Row building (shared)
# ---------------------------------------------------------------------------

def _samples_to_rows(samples: Iterable[dict], clip_id: int, fps: float, source: str) -> list[tuple]:
    rows: list[tuple] = []
    seen_frames: set[int] = set()
    for sample in samples:
        t = float(sample.get("timestamp_seconds", 0.0))
        frame_index = int(round(t * fps))
        if frame_index in seen_frames:
            continue
        seen_frames.add(frame_index)

        lat = sample.get("frame_center_latitude") or sample.get("platform_latitude")
        lon = sample.get("frame_center_longitude") or sample.get("platform_longitude")
        if lat is None or lon is None:
            continue

        telemetry = {
            "source": source,
            "timestamp_seconds": round(t, 3),
            "platform_heading": sample.get("platform_heading"),
            "platform_pitch": sample.get("platform_pitch"),
            "platform_roll": sample.get("platform_roll"),
            "platform_latitude": sample.get("platform_latitude", lat),
            "platform_longitude": sample.get("platform_longitude", lon),
            "platform_altitude_msl": sample.get("platform_altitude_msl"),
            "sensor_azimuth": sample.get("sensor_azimuth"),
            "sensor_elevation": sample.get("sensor_elevation"),
            "sensor_horizontal_fov": sample.get("sensor_horizontal_fov"),
            "sensor_vertical_fov": sample.get("sensor_vertical_fov"),
            "frame_center_latitude": lat,
            "frame_center_longitude": lon,
            "frame_center_elevation": sample.get("frame_center_elevation"),
        }
        telemetry = {k: v for k, v in telemetry.items() if v is not None}
        telemetry["source"] = source
        telemetry["timestamp_seconds"] = round(t, 3)

        footprint_wkt = _footprint_wkt(telemetry, lat, lon)
        rows.append((clip_id, frame_index, round(t, 3), json.dumps(telemetry), footprint_wkt))
    return rows


def _footprint_wkt(telemetry: dict, lat: float, lon: float) -> str:
    """Best-effort camera footprint polygon.

    If horizontal FOV + slant range / altitude are present, project a
    rectangle on the WGS-84 ellipsoid. Otherwise emit a small square
    around the frame center so the schema's NOT-NULL footprint is honoured.
    """
    altitude = telemetry.get("platform_altitude_msl") or 200.0
    hfov = telemetry.get("sensor_horizontal_fov") or 30.0
    vfov = telemetry.get("sensor_vertical_fov") or hfov * 0.6
    azimuth = telemetry.get("sensor_azimuth") or telemetry.get("platform_heading") or 0.0

    try:
        ground_w = 2.0 * altitude * math.tan(math.radians(hfov / 2.0))
        ground_h = 2.0 * altitude * math.tan(math.radians(vfov / 2.0))
    except (TypeError, ValueError):
        ground_w = ground_h = 50.0

    half_w_deg = (ground_w / 2.0) / 111_320.0
    half_h_deg = (ground_h / 2.0) / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    cos_a = math.cos(math.radians(azimuth))
    sin_a = math.sin(math.radians(azimuth))

    corners = []
    for dx, dy in ((-half_w_deg, -half_h_deg), (-half_w_deg, half_h_deg),
                   (half_w_deg, half_h_deg), (half_w_deg, -half_h_deg)):
        rx = dx * cos_a - dy * sin_a
        ry = dx * sin_a + dy * cos_a
        corners.append((lon + rx, lat + ry))
    corners.append(corners[0])
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in corners) + "))"


def _fixture_rows(clip_id: int, duration: float, fps: float) -> list[tuple]:
    """Synthetic fallback identical in shape to a real extractor."""
    frame_step = max(1, int(fps * 2))
    total_frames = max(8, int((duration or 16) * fps))
    base_lat, base_lon = 25.078, 55.179
    rows: list[tuple] = []
    for frame in range(0, total_frames, frame_step):
        t = frame / fps
        lat = base_lat + math.sin(t / 20) * 0.006
        lon = base_lon + math.cos(t / 18) * 0.006
        telemetry = {
            "source": "fixture",
            "timestamp_seconds": round(t, 3),
            "platform_heading": round((t * 7) % 360, 2),
            "sensor_azimuth": round((t * 13) % 360, 2),
            "sensor_elevation": -23.6,
            "platform_latitude": lat + 0.015,
            "platform_longitude": lon - 0.012,
            "frame_center_latitude": lat,
            "frame_center_longitude": lon,
        }
        footprint = _footprint_wkt(telemetry, lat, lon)
        rows.append((clip_id, frame, round(t, 3), json.dumps(telemetry), footprint))
    return rows
