"""FMV (full-motion video) helpers: HLS transcode, ffprobe, telemetry stubs.

Used by the FMV ingest router. Side effects (ffmpeg subprocesses) are kept in
this module to keep the router lean.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from geometry import make_square_feature


def fmv_public_url(hls_path: Optional[str], file_path: str) -> str:
    """Translate an absolute on-disk path into the nginx ``/fmv/...`` URL
    rooted at ``FMV_PATH``. Returns the raw path on any error so the caller
    can fall through to a debug message rather than 500-ing."""
    path = hls_path or file_path
    fmv_root = Path(os.getenv("FMV_PATH", "/data/fmv"))
    try:
        rel = Path(path).resolve().relative_to(fmv_root.resolve())
        return f"/fmv/{rel.as_posix()}"
    except Exception:
        return path


def probe_video(path: Path) -> dict:
    """Wrap ``ffprobe`` and surface only what the FMV catalog actually consumes."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path)
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        data = json.loads(result.stdout or "{}")
    except Exception:
        return {"duration_seconds": 0, "width": None, "height": None, "fps": None, "streams": []}

    video_stream = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), {})
    fps = None
    rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    if rate and rate != "0/0":
        try:
            num, den = rate.split("/")
            fps = float(num) / float(den)
        except Exception:
            fps = None
    return {
        "duration_seconds": float(data.get("format", {}).get("duration") or 0),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps": fps,
        "streams": data.get("streams", []),
    }


def transcode_hls(input_path: Path, clip_dir: Path) -> Optional[Path]:
    """Stream-copy the source video into an HLS VOD playlist in ``clip_dir``.

    Falls back to copying the raw file (and returning ``None``) when ffmpeg
    fails — the catalog row is still useful even without HLS playback.
    """
    clip_dir.mkdir(parents=True, exist_ok=True)
    hls_path = clip_dir / "index.m3u8"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(input_path),
                "-map", "0:v:0", "-map", "0:a?", "-c:v", "copy", "-c:a", "aac",
                "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod",
                "-hls_segment_filename", str(clip_dir / "segment_%05d.ts"),
                str(hls_path),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        return hls_path
    except Exception:
        shutil.copy2(input_path, clip_dir / input_path.name)
        return None


def telemetry_rows_for_clip(clip_id: int, duration: float, fps: Optional[float]) -> list[tuple]:
    """Synthesise placeholder MISB-0601 rows when an uploaded clip has no KLV.

    The real telemetry extractor is in :mod:`video_metadata`; this function is
    only used as a fixture for clips that lack a sidecar SRT and do not embed
    any 0601 KLV stream. Returns rows formatted for the ``fmv_frames`` insert.
    """
    frame_step = max(1, int((fps or 30) * 2))
    total_frames = max(8, int((duration or 16) * (fps or 30)))
    rows = []
    base_lat, base_lon = 25.078, 55.179
    for frame in range(0, total_frames, frame_step):
        t = frame / (fps or 30)
        lat = base_lat + math.sin(t / 20) * 0.006
        lon = base_lon + math.cos(t / 18) * 0.006
        footprint = make_square_feature(lon, lat, 0.006, {"clip_id": clip_id, "frame": frame})["geometry"]["coordinates"][0]
        footprint_wkt = "POLYGON((" + ", ".join(f"{x} {y}" for x, y in footprint) + "))"
        telemetry = {
            "source": "misb-klv" if duration else "fixture",
            "timestamp_seconds": round(t, 3),
            "platform_heading": round((t * 7) % 360, 2),
            "sensor_azimuth": round((t * 13) % 360, 2),
            "sensor_elevation": -23.6,
            "platform_latitude": lat + 0.015,
            "platform_longitude": lon - 0.012,
            "frame_center_latitude": lat,
            "frame_center_longitude": lon,
        }
        rows.append((clip_id, frame, t, json.dumps(telemetry), footprint_wkt))
    return rows
