import hashlib
import math
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


# Phase 5.21: SAR-specific metadata keys. Incidence angle determines whether
# layover/foreshortening artifacts are likely (low incidence on tall buildings
# = bright distortion that the optical-trained detectors mis-fire on); look
# direction (LEFT/RIGHT) tells the analyst which side of a vertical feature
# the shadow falls on. Both are surfaced to the UI so the analyst can
# correctly interpret a SAR detection's geometry.
SAR_INCIDENCE_KEYS = (
    "incidence_angle",
    "incidence_angle_degrees",
    "incidence_angle_center",
    "centre_incidence_angle",
    "INCIDENCE_ANGLE",
    "INCIDENCE_NEAR",
    "INCIDENCE_FAR",
    "S1_INCIDENCE_ANGLE",
    "sar:incidence_angle",
)
SAR_LOOK_DIRECTION_KEYS = (
    "look_direction",
    "antenna_pointing",
    "LOOK_DIRECTION",
    "PASS_DIRECTION",
    "ORBIT_DIRECTION",
    "sar:looks_direction",
)
SAR_POLARIZATION_KEYS = (
    "polarization",
    "POLARIZATION",
    "POLARISATIONS",
    "S1_POLARIZATIONS",
    "sar:polarizations",
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


def _lookup_first(tags: dict, keys: tuple[str, ...]) -> Optional[str]:
    lower_tags = {str(k).lower(): v for k, v in (tags or {}).items()}
    for key in keys:
        value = lower_tags.get(key.lower())
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize_look_direction(value: str | None) -> Optional[str]:
    """Phase 5.21: map vendor look-direction strings to ``LEFT`` / ``RIGHT``
    / ``ASCENDING`` / ``DESCENDING``. Sentinel-1 reports ASC/DESC for the
    pass and ``RIGHT`` for typical look; other vendors use ``L`` / ``R``.
    Returns ``None`` when the value can't be confidently mapped.
    """
    if not value:
        return None
    v = str(value).strip().upper()
    if v in {"LEFT", "L", "PORT"}:
        return "LEFT"
    if v in {"RIGHT", "R", "STARBOARD"}:
        return "RIGHT"
    if v in {"ASCENDING", "ASC"}:
        return "ASCENDING"
    if v in {"DESCENDING", "DESC", "DSC"}:
        return "DESCENDING"
    return None


def parse_sar_metadata(tags: dict) -> dict:
    """Phase 5.21: extract SAR-specific fields from raster tags.

    Surfaces ``incidence_angle_deg``, ``look_direction``,
    ``orbit_direction``, ``polarizations`` when present in the tags. Empty
    dict when no SAR tags found. The worker writes these onto each
    detection's ``imagery_metadata`` so the UI can render a layover-risk
    indicator when incidence is low (< 25° = high layover risk) — telling
    the analyst that a SAR-derived detection's geometry may be distorted.
    """
    if not tags:
        return {}
    out: dict = {}
    incidence_text = _lookup_first(tags, SAR_INCIDENCE_KEYS)
    if incidence_text:
        try:
            out["incidence_angle_deg"] = round(float(str(incidence_text).split()[0]), 3)
        except (TypeError, ValueError):
            pass
    look_raw = _lookup_first(tags, SAR_LOOK_DIRECTION_KEYS)
    if look_raw:
        normalized = _normalize_look_direction(look_raw)
        if normalized in {"LEFT", "RIGHT"}:
            out["look_direction"] = normalized
        elif normalized in {"ASCENDING", "DESCENDING"}:
            out["orbit_direction"] = normalized
            # Sentinel-1 defaults to RIGHT-looking unless reprogrammed.
            out.setdefault("look_direction", "RIGHT")
        else:
            out["look_direction_raw"] = look_raw
    pol_raw = _lookup_first(tags, SAR_POLARIZATION_KEYS)
    if pol_raw:
        out["polarizations"] = [
            p.strip().upper()
            for p in pol_raw.replace("+", " ").replace(",", " ").split()
            if p.strip()
        ]
    if out.get("incidence_angle_deg") is not None:
        angle = out["incidence_angle_deg"]
        out["layover_risk"] = (
            "high" if angle < 25.0 else "moderate" if angle < 35.0 else "low"
        )
    return out


# Ground resolution (m/px) of a 256-px WebMercatorQuad tile at zoom 0, at the
# equator. Each zoom level halves it: res(z) = _WEBMERCATOR_Z0_RES / 2**z.
_WEBMERCATOR_Z0_RES = 156543.03392804097


def native_max_zoom(metadata: dict, default: int = 18) -> int:
    """Best WebMercatorQuad zoom level for a COG's native pixel resolution.

    Past this zoom TiTiler only ever upsamples its highest overview — wasted
    round-trips that produce tiles no sharper than what the frontend can get
    by upscaling the native-zoom tile client-side. The map's SAT ``TileLayer``
    feeds this value into Leaflet's ``maxNativeZoom`` so it stops fetching
    once the COG runs out of real pixels.

    Derives ground sample distance from the stored raster ``width`` and
    ``bounds`` (both written by :func:`extract_raster_metadata`). Returns
    ``default`` when those tags are missing or the geometry is degenerate, so
    callers never have to special-case a ``None``.
    """
    try:
        width = float(metadata.get("width") or 0)
        bounds = metadata.get("bounds") or {}
        left = float(bounds["left"])
        right = float(bounds["right"])
    except (TypeError, ValueError, KeyError, AttributeError):
        return default
    span = right - left
    if width <= 0 or span <= 0:
        return default

    crs = str(metadata.get("crs") or "").upper()
    if "4326" in crs or "CRS84" in crs:
        # Geographic CRS: bounds are degrees. Convert the x-span to metres at
        # the scene's centre latitude (longitude degrees shrink with cos lat).
        try:
            top = float(bounds["top"])
            bottom = float(bounds["bottom"])
        except (TypeError, ValueError, KeyError):
            return default
        cos_lat = max(math.cos(math.radians((top + bottom) / 2.0)), 0.01)
        gsd = (span / width) * 111320.0 * cos_lat
    else:
        # Projected CRS (UTM, Web Mercator, ...): bounds are already metres.
        gsd = span / width
    if gsd <= 0:
        return default

    zoom = round(math.log2(_WEBMERCATOR_Z0_RES / gsd))
    return max(10, min(24, int(zoom)))


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
            # Phase 5.21: SAR-specific fields surface as top-level metadata so
            # downstream UIs / threat rules can use them without re-parsing
            # the raw tags blob. The function returns {} for optical rasters,
            # so this is a no-op when no SAR tags are present.
            sar_fields = parse_sar_metadata(tags)
            if sar_fields:
                metadata["sar"] = sar_fields
    except Exception as exc:
        metadata["metadata_error"] = str(exc)
    return metadata
