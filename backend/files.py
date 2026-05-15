"""Upload-file helpers shared by the API routers."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException, UploadFile


def safe_filename(filename: str) -> str:
    """Strip directory components and unsafe characters from a user-supplied filename."""
    name = Path(filename or "upload.tif").name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "upload.tif"


def save_upload_file(file: UploadFile, local_path: Path, chunk_size: int = 1024 * 1024) -> int:
    """Stream a multipart upload to disk in fixed-size chunks. Returns total bytes written."""
    size = 0
    try:
        with local_path.open("wb") as handle:
            while True:
                chunk = file.file.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                handle.write(chunk)
    finally:
        file.file.close()
    return size


def classify_upload(filename: str) -> tuple[str, str]:
    """Map a filename's extension to ``(media_type, celery_task_name)``.

    Raises HTTP 400 for unsupported extensions so the ingest endpoint can
    surface a tidy error to the operator.
    """
    suffix = Path(filename).suffix.lower()
    if suffix in {".tif", ".tiff", ".jp2", ".j2k", ".nc", ".netcdf", ".png", ".jpg", ".jpeg", ".nitf", ".ntf"}:
        return "imagery", "workers.raster.process"
    if suffix in {".mp4", ".mov", ".m4v", ".ts", ".mpeg", ".mpg"}:
        return "fmv", "worker.process_fmv"
    if suffix in {".geojson", ".json", ".kml", ".kmz", ".zip", ".shp", ".gpkg"}:
        return "vector", "workers.vector.process"
    if suffix in {".pdf", ".txt", ".csv", ".xlsx", ".docx"}:
        return "document", "workers.document.process"
    if suffix in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".amr"}:
        return "audio", "workers.audio.transcribe"
    if suffix in {".b3dm", ".i3dm", ".pnts", ".glb", ".gltf"}:
        return "3d", "workers.tiles3d.process"
    raise HTTPException(status_code=400, detail=f"Unsupported upload format: {suffix or 'unknown'}")
