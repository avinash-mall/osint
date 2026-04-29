import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


TIME_TAG_KEYS = (
    "acquisition_time",
    "acquisition_datetime",
    "acquired",
    "datetime",
    "date_time",
    "TIFFTAG_DATETIME",
    "NITF_IDATIM",
    "NITF_IDATIM",
    "PRODUCT_START_TIME",
    "SENSING_TIME",
    "SCENE_CENTER_TIME",
    "IMAGING_TIME",
)


def file_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_time(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # NITF IDATIM frequently uses YYYYMMDDHHMMSS.
    if re.fullmatch(r"\d{14}", text):
        try:
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None

    # TIFF DateTime commonly uses YYYY:MM:DD HH:MM:SS.
    if re.fullmatch(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}", text):
        try:
            return datetime.strptime(text, "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            continue
    return None


def parse_metadata_time(tags: dict) -> Optional[str]:
    lower_tags = {str(key).lower(): value for key, value in (tags or {}).items()}
    for key in TIME_TAG_KEYS:
        value = lower_tags.get(key.lower())
        parsed = _normalize_time(value)
        if parsed:
            return parsed
    for key, value in lower_tags.items():
        if "time" in key or "date" in key:
            parsed = _normalize_time(value)
            if parsed:
                return parsed
    return None


def extract_raster_metadata(path: str | Path, include_hash: bool = True) -> dict:
    metadata: dict = {
        "source_filename": Path(path).name,
    }
    if include_hash:
        metadata["source_hash"] = file_sha256(path)
    try:
        import rasterio

        with rasterio.open(path) as src:
            tags = dict(src.tags() or {})
            for namespace in src.tag_namespaces() or []:
                try:
                    namespaced = src.tags(ns=namespace)
                except Exception:
                    namespaced = {}
                for key, value in namespaced.items():
                    tags[f"{namespace}:{key}"] = value

            metadata.update({
                "driver": src.driver,
                "width": src.width,
                "height": src.height,
                "band_count": src.count,
                "crs": str(src.crs) if src.crs else None,
                "dtypes": list(src.dtypes or []),
                "bounds": {
                    "left": src.bounds.left,
                    "bottom": src.bounds.bottom,
                    "right": src.bounds.right,
                    "top": src.bounds.top,
                },
                "tags": {str(key): str(value) for key, value in tags.items()},
            })
            acq_time = parse_metadata_time(tags)
            if acq_time:
                metadata["acquisition_time"] = acq_time
    except Exception as exc:
        metadata["metadata_error"] = str(exc)
    return metadata
