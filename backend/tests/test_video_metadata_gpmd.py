"""Offline unit tests for backend/video_metadata.py GPMD extraction and the
camera-footprint rotation. Regression guards for the 2026-06-12 audit fixes:

* ``_extract_gpmd`` spread all GPS5 samples across [0,1) s regardless of clip
  duration, so ``_samples_to_rows``'s per-frame dedup discarded almost all
  telemetry — it now threads ``duration_s`` exactly like ``_extract_klv``.
* ``_footprint_wkt`` converted metre half-spans to degrees BEFORE rotating
  (anisotropic distortion away from the equator) and rotated CCW while sensor
  azimuth is clockwise-from-north — it now rotates the metre offsets with the
  CW convention, then converts to degrees.

ffprobe/ffmpeg are monkeypatched; no media files or binaries needed.
"""
from __future__ import annotations

import json
import math
import re
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import video_metadata  # noqa: E402


class _Proc:
    def __init__(self, stdout):
        self.returncode = 0
        self.stdout = stdout


def _gpmd_payload(points):
    blob = b"SCAL" + b"l" + bytes([4]) + struct.pack(">H", 5)
    blob += struct.pack(">5l", 10_000_000, 10_000_000, 1000, 1000, 100)
    for lat, lon, alt in points:
        blob += b"GPS5" + b"l" + bytes([20]) + struct.pack(">H", 1)
        blob += struct.pack(">5l", int(lat * 1e7), int(lon * 1e7), int(alt * 1000), 0, 0)
    return blob


def test_gpmd_samples_spread_across_clip_duration(monkeypatch):
    points = [(25.0 + i * 0.001, 55.0, 100.0) for i in range(5)]

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return _Proc(json.dumps({"streams": [
                {"index": 2, "codec_type": "data", "codec_tag_string": "gpmd", "tags": {}},
            ]}))
        return _Proc(_gpmd_payload(points))

    monkeypatch.setattr(video_metadata.subprocess, "run", fake_run)
    samples = video_metadata._extract_gpmd(Path("/tmp/clip.mp4"), 120.0)
    assert len(samples) == 5
    timestamps = [s["timestamp_seconds"] for s in samples]
    assert timestamps == [0.0, 24.0, 48.0, 72.0, 96.0]

    rows = video_metadata._samples_to_rows(samples, clip_id=1, fps=30.0, source="gpmd")
    # Pre-fix all five samples landed inside the first second and the
    # per-frame dedup collapsed them to ~2 rows.
    assert len(rows) == 5


def _wkt_corners(wkt):
    coords = re.search(r"POLYGON\(\((.+)\)\)", wkt).group(1)
    return [tuple(float(v) for v in pair.split()) for pair in coords.split(", ")][:-1]


def test_footprint_rotation_is_clockwise_and_isotropic():
    lat, lon = 60.0, 10.0
    telemetry = {
        "platform_altitude_msl": 1000.0,
        "sensor_horizontal_fov": 60.0,
        "sensor_vertical_fov": 30.0,
    }
    ground_w = 2.0 * 1000.0 * math.tan(math.radians(30.0))
    ground_h = 2.0 * 1000.0 * math.tan(math.radians(15.0))
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))

    corners_north = _wkt_corners(
        video_metadata._footprint_wkt({**telemetry, "sensor_azimuth": 0.0}, lat, lon)
    )
    lon_span = max(c[0] for c in corners_north) - min(c[0] for c in corners_north)
    lat_span = max(c[1] for c in corners_north) - min(c[1] for c in corners_north)
    assert lon_span * m_per_deg_lon == pytest.approx(ground_w, rel=1e-6)
    assert lat_span * m_per_deg_lat == pytest.approx(ground_h, rel=1e-6)

    # Azimuth 90° (camera facing east): the footprint's long axis (width)
    # must now span latitude and the short axis (height) longitude — in
    # METRES, at any latitude. The pre-fix deg-space rotation skewed this
    # by the cos(lat) anisotropy (2x at 60°N).
    corners_east = _wkt_corners(
        video_metadata._footprint_wkt({**telemetry, "sensor_azimuth": 90.0}, lat, lon)
    )
    lon_span = max(c[0] for c in corners_east) - min(c[0] for c in corners_east)
    lat_span = max(c[1] for c in corners_east) - min(c[1] for c in corners_east)
    assert lon_span * m_per_deg_lon == pytest.approx(ground_h, rel=1e-6)
    assert lat_span * m_per_deg_lat == pytest.approx(ground_w, rel=1e-6)
