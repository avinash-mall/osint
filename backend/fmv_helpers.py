"""FMV (full-motion video) helpers: HLS transcode and ffprobe.

Used by the FMV ingest router. Side effects (ffmpeg subprocesses) are kept in
this module to keep the router lean.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


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
            timeout=_env_int("FMV_PROBE_TIMEOUT_S", 30),
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
            timeout=_env_int("FMV_TRANSCODE_TIMEOUT_S", 900),
        )
        return hls_path
    except Exception:
        fallback_path = clip_dir / input_path.name
        if input_path.resolve() != fallback_path.resolve():
            shutil.copy2(input_path, fallback_path)
        return None
