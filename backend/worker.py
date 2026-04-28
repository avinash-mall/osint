import os
import json
import requests
import subprocess
import uuid
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse
from celery import Celery
from database import db, postgis_db
import rasterio
from rasterio.windows import Window
from rasterio.transform import xy
from shapely.geometry import Polygon, MultiPolygon
import numpy as np
from PIL import Image
from ai import AIUnavailable, ai_status, get_llm_json
from imagery_metadata import extract_raster_metadata
from threat_assessment import assess_detection_threat, clean_detection_class, conservative_detection_ontology

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://localhost:8001")
IMAGERY_PATH = os.getenv("IMAGERY_PATH", "/data/imagery")

logger = logging.getLogger(__name__)

celery_app = Celery("sentinelos_worker", broker=REDIS_URL, backend=REDIS_URL)


def ensure_worker_imagery_schema() -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'")
        cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64)")
        cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS source_filename VARCHAR(255)")
        cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_passes_source_time ON satellite_passes(source_hash, acquisition_time)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS detection_target_candidates (
                id SERIAL PRIMARY KEY,
                detection_id INTEGER REFERENCES detections(id) ON DELETE CASCADE,
                target_id VARCHAR(255) NOT NULL,
                target_name VARCHAR(255),
                score REAL DEFAULT 0,
                reason TEXT,
                status VARCHAR(50) DEFAULT 'pending',
                evidence JSONB DEFAULT '{}',
                reviewed_by VARCHAR(100),
                reviewed_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE (detection_id, target_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_detection_target_candidates_detection ON detection_target_candidates(detection_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_detection_target_candidates_target ON detection_target_candidates(target_id, status)")


def record_timeline_event(domain: str, event_type: str, title: str, payload: dict, occurred_at: str = None) -> None:
    try:
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO timeline_events (domain, event_type, title, payload, occurred_at)
                VALUES (%s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()))
            """, (domain, event_type, title, json.dumps(payload or {}, default=str), occurred_at))
    except Exception as exc:
        logger.warning("Failed to record timeline event: %s", exc)


def publish_event(topic: str, payload: dict) -> None:
    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True)
        client.publish(f"events:{topic}", json.dumps(payload, default=str))
        client.close()
    except Exception as e:
        logger.warning("Failed to publish %s event: %s", topic, e)


def update_upload_job(upload_id: str = None, file_path: str = None, status: str = None, metadata: dict = None) -> None:
    if not upload_id and not file_path:
        return

    clauses = []
    params = []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if metadata:
        clauses.append("metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb")
        params.append(json.dumps(metadata, default=str))
    clauses.append("updated_at = NOW()")

    where = []
    if upload_id:
        where.append("upload_id = %s")
        params.append(upload_id)
    if file_path:
        where.append("file_path = %s")
        params.append(file_path)

    try:
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(f"UPDATE upload_jobs SET {', '.join(clauses)} WHERE {' OR '.join(where)}", params)
    except Exception as exc:
        logger.warning("Failed to update upload job status: %s", exc)


def get_upload_job(upload_id: str = None) -> dict:
    if not upload_id:
        return {}
    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute("SELECT filename, metadata FROM upload_jobs WHERE upload_id = %s", (upload_id,))
            row = cursor.fetchone()
            return dict(row) if row else {}
    except Exception:
        return {}


def report_progress(task, upload_id: str, file_path: str, stage: str, progress: int, message: str, extra: dict = None) -> None:
    progress = max(0, min(100, int(progress)))
    payload = {
        "upload_id": upload_id,
        "stage": stage,
        "progress": progress,
        "message": message,
        **(extra or {}),
    }
    update_upload_job(upload_id=upload_id, file_path=file_path, metadata=payload)
    if task:
        task.update_state(state="PROGRESS", meta=payload)
    publish_event("imagery", {"type": "imagery_progress", **payload})
    publish_event("ops", {"type": "imagery_progress", **payload})


def ensure_cog(input_path: str, output_path: str) -> str:
    """Convert any raster to Cloud Optimized GeoTIFF."""
    if input_path.endswith(".nc") or input_path.endswith(".netcdf"):
        # Handle NetCDF via rioxarray
        try:
            import rioxarray
            ds = rioxarray.open_rasterio(input_path)
            # If time dimension exists, take the first slice
            if "time" in ds.dims:
                ds = ds.isel(time=0)
            # If band dimension > 1 and we want RGB or single band
            if "band" in ds.dims and ds.sizes.get("band", 0) > 1:
                ds = ds.isel(band=0)
            ds.rio.to_raster(output_path, driver="COG", compress="DEFLATE")
            return output_path
        except Exception as e:
            raise RuntimeError(f"NetCDF conversion failed: {e}")
    else:
        # GeoTIFF / JP2 -> COG via GDAL
        cmd = [
            "gdal_translate",
            input_path,
            output_path,
            "-of", "COG",
            "-co", "COMPRESS=DEFLATE",
            "-co", "OVERVIEWS=AUTO"
        ]
        subprocess.run(cmd, check=True)
        return output_path


def get_raster_footprint(cog_path: str):
    """Extract bounding box as a Shapely Polygon in EPSG:4326."""
    with rasterio.open(cog_path) as src:
        bounds = src.bounds
        crs = src.crs
        # Reproject bounds to WGS84 if needed
        if crs and crs.to_string() != "EPSG:4326":
            from rasterio.warp import transform_bounds
            min_lon, min_lat, max_lon, max_lat = transform_bounds(
                crs, "EPSG:4326", bounds.left, bounds.bottom, bounds.right, bounds.top
            )
        else:
            min_lon, min_lat, max_lon, max_lat = bounds.left, bounds.bottom, bounds.right, bounds.top
        
        footprint = MultiPolygon([Polygon([
            (min_lon, min_lat),
            (min_lon, max_lat),
            (max_lon, max_lat),
            (max_lon, min_lat),
            (min_lon, min_lat)
        ])])
        return footprint, min_lon, min_lat, max_lon, max_lat


def resolve_input_path(image_url: str) -> str:
    """Resolve local, HTTP(S), or unsupported remote imagery references into a local file."""
    parsed = urlparse(image_url)
    incoming_dir = os.path.join(IMAGERY_PATH, "incoming")
    os.makedirs(incoming_dir, exist_ok=True)

    if parsed.scheme in ("http", "https"):
        filename = os.path.basename(parsed.path) or f"{uuid.uuid4()}.tif"
        input_path = os.path.join(incoming_dir, filename)
        with requests.get(image_url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with open(input_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        return input_path

    if parsed.scheme == "s3":
        raise RuntimeError("s3:// imagery ingestion requires an S3 client configuration and is not enabled.")

    if parsed.scheme and parsed.scheme != "file":
        raise RuntimeError(f"Unsupported imagery URL scheme: {parsed.scheme}")

    local_path = parsed.path if parsed.scheme == "file" else image_url
    if os.path.exists(local_path):
        return local_path

    filename = os.path.basename(local_path)
    input_path = os.path.join(incoming_dir, filename)
    if os.path.exists(input_path):
        return input_path

    raise FileNotFoundError(f"Imagery file not found: {image_url}")


def chip_to_uint8_rgb(chip: np.ndarray) -> np.ndarray:
    chip_rgb = chip[:3] if chip.shape[0] >= 3 else np.repeat(chip[:1], 3, axis=0)
    chip_rgb = np.nan_to_num(chip_rgb.astype("float32"), nan=0.0, posinf=0.0, neginf=0.0)
    if chip_rgb.dtype != np.uint8:
        low, high = np.percentile(chip_rgb, [2, 98])
        if high > low:
            chip_rgb = np.clip((chip_rgb - low) / (high - low) * 255, 0, 255).astype(np.uint8)
        else:
            chip_rgb = np.zeros_like(chip_rgb, dtype=np.uint8)
    return np.moveaxis(chip_rgb, 0, -1)


def bbox_iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_area = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)
    if inter_area == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter_area
    return inter_area / union if union else 0.0


def deduplicate_detections(detections: list, iou_threshold: float = 0.45) -> list:
    kept = []
    for det in sorted(detections, key=lambda item: item.get("confidence", 0), reverse=True):
        if not det.get("pixel_bbox"):
            kept.append(det)
            continue
        duplicate = any(
            det.get("class") == existing.get("class")
            and bbox_iou(det["pixel_bbox"], existing.get("pixel_bbox", [])) >= iou_threshold
            for existing in kept
            if existing.get("pixel_bbox")
        )
        if not duplicate:
            kept.append(det)
    return kept


def clean_detection_class(det_class: str) -> str:
    label = (det_class or "Unknown").replace("_", " ").replace("-", " ").strip()
    prefixes = ("xview ", "dota ", "fair1m ", "fmow ", "rareplanes ")
    lower = label.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            label = label[len(prefix):]
            break
    return " ".join(part.capitalize() for part in label.split()) or "Unknown"


def detection_ontology(det_class: str) -> dict:
    return conservative_detection_ontology(det_class)


def llm_detection_ontology(det_class: str, count: int, avg_confidence: float) -> dict:
    base = conservative_detection_ontology(det_class, confidence=avg_confidence)
    prompt = json.dumps({
        "task": "Classify a GEOINT computer-vision detection class for upload processing metadata.",
        "input": {
            "raw_class": det_class,
            "fallback_label": base["label"],
            "fallback_category": base["category"],
            "fallback_threat_level": base["threat_level"],
            "count_in_image": count,
            "avg_confidence": avg_confidence,
        },
        "required_json_schema": {
            "label": "short human label",
            "domain": "GEOINT",
            "category": "one of air, maritime, ground, combat, infrastructure, logistics, energy, facility, unknown",
            "threat_level": "one of low, medium, high, critical",
            "description": "one short analyst-facing sentence",
            "recommended_filter": "short filter chip text",
        },
    }, default=str)
    system = "Return only compact JSON. Do not invent sightings or facts. Use only the class name and counts provided."
    data = get_llm_json(prompt, system=system, max_tokens=240, timeout_seconds=6)
    return {
        **base,
        "label": str(data.get("label") or base["label"])[:80],
        "domain": "GEOINT",
        "category": base["category"],
        "threat_level": base["threat_level"],
        "threat_confidence": base["threat_confidence"],
        "assessment_status": base["assessment_status"],
        "evidence": base["evidence"],
        "description": str(data.get("description") or base["description"])[:280],
        "recommended_filter": str(data.get("recommended_filter") or data.get("label") or base["recommended_filter"])[:80],
        "generated_by": f"{ai_status().get('model') or 'llm'}; threat=deterministic-rules",
        "status": "ok",
    }


def classify_detection_ontologies(detections: list, progress_callback=None) -> dict[str, dict]:
    grouped: dict[str, list[float]] = {}
    for det in detections:
        det_class = det.get("class", "Unknown")
        grouped.setdefault(det_class, []).append(float(det.get("confidence") or 0))

    ontology_by_class: dict[str, dict] = {}
    total = max(1, len(grouped))
    for index, (det_class, confidences) in enumerate(grouped.items(), start=1):
        if progress_callback:
            progress_callback(
                "llm_classification",
                90 + int((index - 1) / total * 4),
                f"Queueing LLM classification for detection classes ({index}/{total}).",
                {"llm_classes_processed": index - 1, "llm_classes_total": total},
            )
        try:
            ontology_by_class[det_class] = llm_detection_ontology(
                det_class,
                count=len(confidences),
                avg_confidence=sum(confidences) / max(1, len(confidences)),
            )
        except AIUnavailable as exc:
            ontology_by_class[det_class] = {
                **detection_ontology(det_class),
                "description": str(exc),
                "status": "unavailable",
            }
    if progress_callback:
        progress_callback(
            "llm_classification",
            94,
            "Detection class LLM classification complete.",
            {"llm_classes_processed": len(grouped), "llm_classes_total": len(grouped)},
        )
    return ontology_by_class


def slice_and_infer(cog_path: str, pass_id: int, chip_size: int = 640, overlap: int = 100, progress_callback=None):
    """
    Slice COG into chips, send to inference service, and store results in PostGIS + Neo4j.
    """
    with rasterio.open(cog_path) as src:
        width = src.width
        height = src.height
        transform = src.transform
        crs = src.crs
        
        detections = []
        
        # Calculate steps with overlap
        step = chip_size - overlap
        y_steps = list(range(0, height, step))
        x_steps = list(range(0, width, step))
        total_windows = max(1, len(y_steps) * len(x_steps))
        processed_windows = 0
        last_reported_percent = -1

        for y in y_steps:
            for x in x_steps:
                processed_windows += 1
                if progress_callback:
                    inferred_percent = int(processed_windows / total_windows * 100)
                    if inferred_percent >= last_reported_percent + 5 or processed_windows == total_windows:
                        last_reported_percent = inferred_percent
                        progress_callback(
                            "inference",
                            55 + int(inferred_percent * 0.35),
                            f"Running inference on raster chips ({processed_windows}/{total_windows}).",
                            {"processed_chips": processed_windows, "total_chips": total_windows},
                        )
                win_width = min(chip_size, width - x)
                win_height = min(chip_size, height - y)
                window = Window(x, y, win_width, win_height)
                
                # Read chip
                chip = src.read(window=window)
                
                # Skip mostly empty / nodata chips
                if np.all(chip == 0) or (src.nodata is not None and np.all(chip == src.nodata)):
                    continue
                
                # Save temporary chip image
                chip_filename = f"chip_{pass_id}_{x}_{y}.png"
                chip_path = os.path.join(IMAGERY_PATH, "chips", chip_filename)
                os.makedirs(os.path.dirname(chip_path), exist_ok=True)
                
                # Write a display-scaled RGB chip. This handles float, single-band,
                # and multispectral rasters without relying on PNG geotags.
                Image.fromarray(chip_to_uint8_rgb(chip), mode="RGB").save(chip_path)
                
                # Inference
                try:
                    with open(chip_path, "rb") as f:
                        resp = requests.post(
                            f"{INFERENCE_URL}/detect",
                            files={"image": f},
                            data={"metadata": f'{{"pass_id": {pass_id}, "window": [{x}, {y}, {win_width}, {win_height}]}}'},
                            timeout=60
                        )
                    resp.raise_for_status()
                    chip_detections = resp.json().get("detections", [])

                    for det in chip_detections:
                        # Convert normalized bbox to pixel coords in chip
                        cx, cy, w, h = det["bbox"]  # normalized [x_center, y_center, width, height]
                        chip_px_cx = cx * win_width
                        chip_px_cy = cy * win_height
                        chip_px_w = w * win_width
                        chip_px_h = h * win_height

                        # Convert to absolute pixel coords in full image
                        abs_px_x1 = x + chip_px_cx - chip_px_w / 2
                        abs_px_y1 = y + chip_px_cy - chip_px_h / 2
                        abs_px_x2 = abs_px_x1 + chip_px_w
                        abs_px_y2 = abs_px_y1 + chip_px_h

                        pixel_obb = []
                        if det.get("obb") and len(det["obb"]) == 8:
                            for index, value in enumerate(det["obb"]):
                                if index % 2 == 0:
                                    pixel_obb.append(x + float(value) * win_width)
                                else:
                                    pixel_obb.append(y + float(value) * win_height)
                        else:
                            pixel_obb = [
                                abs_px_x1, abs_px_y1,
                                abs_px_x2, abs_px_y1,
                                abs_px_x2, abs_px_y2,
                                abs_px_x1, abs_px_y2,
                            ]

                        pixel_points = list(zip(pixel_obb[0::2], pixel_obb[1::2]))
                        lons, lats = [], []
                        for px, py in pixel_points:
                            lon, lat = xy(transform, py, px, offset="center")
                            lons.append(lon)
                            lats.append(lat)

                        if crs and crs.to_string() != "EPSG:4326":
                            from rasterio.warp import transform as rasterio_transform
                            lons, lats = rasterio_transform(crs, "EPSG:4326", lons, lats)

                        geo_polygon = [coord for point in zip(lons, lats) for coord in point]
                        lon1, lat1, lon2, lat2 = min(lons), min(lats), max(lons), max(lats)

                        det["pixel_bbox"] = [abs_px_x1, abs_px_y1, abs_px_x2, abs_px_y2]
                        det["pixel_obb"] = pixel_obb
                        det["geo_bbox"] = [lon1, lat1, lon2, lat2]
                        det["geo_polygon"] = geo_polygon
                        detections.append(det)
                    
                except Exception as e:
                    raise RuntimeError(f"Inference failed for chip {chip_path}: {e}") from e
                finally:
                    # Clean up chip file
                    if os.path.exists(chip_path):
                        os.remove(chip_path)
    
    return deduplicate_detections(detections)


def store_detections(detections: list, pass_id: int, ontology_by_class: dict[str, dict] = None):
    """Store detections in PostGIS and create Neo4j nodes."""
    if not detections:
        return 0
    
    with postgis_db.get_cursor(commit=True) as cursor:
        for det in detections:
            lon1, lat1, lon2, lat2 = det["geo_bbox"]
            confidence = det.get("confidence", 0.0)
            det_class = det.get("class", "Unknown")
            ontology = (ontology_by_class or {}).get(det_class) or detection_ontology(det_class)
            assessment = assess_detection_threat(det_class, confidence=confidence, allegiance=det.get("allegiance", "unknown"))
            ontology = {
                **ontology,
                "threat_level": assessment["threat_level"],
                "threat_confidence": assessment["threat_confidence"],
                "assessment_status": assessment["assessment_status"],
                "evidence": assessment["evidence"],
                "category": assessment["category"],
            }
            pixel_bbox = det.get("pixel_bbox", [])
            geo_polygon = det.get("geo_polygon") or [lon1, lat1, lon1, lat2, lon2, lat2, lon2, lat1]
            
            # Create WKT polygons
            pairs = list(zip(geo_polygon[0::2], geo_polygon[1::2]))
            if pairs[0] != pairs[-1]:
                pairs.append(pairs[0])
            geom_wkt = "POLYGON((" + ", ".join(f"{lon} {lat}" for lon, lat in pairs) + "))"
            centroid_wkt = f"POINT({sum(lon for lon, _lat in pairs[:-1]) / max(1, len(pairs) - 1)} {sum(lat for _lon, lat in pairs[:-1]) / max(1, len(pairs) - 1)})"
            
            cursor.execute("""
                INSERT INTO detections (pass_id, class, confidence, geom, centroid, pixel_bbox, metadata)
                VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s, %s)
                RETURNING id
            """, (
                pass_id,
                det_class,
                confidence,
                geom_wkt,
                centroid_wkt,
                json.dumps({"bbox": pixel_bbox, "obb": det.get("pixel_obb", [])}),
                json.dumps({
                    "source": "inference",
                    "chip_size": 640,
                    "geo_polygon": geo_polygon,
                    "confidence": confidence,
                    "ontology": ontology,
                    "threat_level": assessment["threat_level"],
                    "threat_confidence": assessment["threat_confidence"],
                    "assessment_status": assessment["assessment_status"],
                    "evidence": assessment["evidence"],
                    "allegiance": det.get("allegiance", "unknown"),
                })
            ))
            
            det_id = cursor.fetchone()["id"]
            
            # Insert into Neo4j
            with db.get_session() as neo_session:
                neo_session.run("""
                    MATCH (sp:SatellitePass {postgis_id: $pass_id})
                    CREATE (d:Detection {
                        postgis_id: $det_id,
                        class: $det_class,
                        label: $label,
                        confidence: $confidence,
                        threat_level: $threat_level,
                        threat_confidence: $threat_confidence,
                        assessment_status: $assessment_status,
                        ontology_category: $ontology_category,
                        allegiance: $allegiance,
                        latitude: $lat,
                        longitude: $lon,
                        created_at: datetime()
                    })
                    CREATE (sp)-[:CONTAINS_DETECTION]->(d)
                """, {
                    "pass_id": pass_id,
                    "det_id": det_id,
                    "det_class": det_class,
                    "label": ontology["label"],
                    "confidence": confidence,
                    "threat_level": assessment["threat_level"],
                    "threat_confidence": assessment["threat_confidence"],
                    "assessment_status": assessment["assessment_status"],
                    "ontology_category": ontology["category"],
                    "allegiance": det.get("allegiance", "unknown"),
                    "lat": (lat1 + lat2) / 2,
                    "lon": (lon1 + lon2) / 2
                })
    
    return len(detections)


def clear_existing_detections(pass_id: int) -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT id FROM detections WHERE pass_id = %s", (pass_id,))
        det_ids = [row["id"] for row in cursor.fetchall()]
        cursor.execute("DELETE FROM detections WHERE pass_id = %s", (pass_id,))

    if det_ids:
        with db.get_session() as neo_session:
            neo_session.run("""
                MATCH (d:Detection)
                WHERE d.postgis_id IN $det_ids
                DETACH DELETE d
            """, {"det_ids": det_ids})


def target_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    d_phi = np.radians(lat2 - lat1)
    d_lambda = np.radians(lon2 - lon1)
    a = np.sin(d_phi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(d_lambda / 2) ** 2
    return float(radius * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a)))


def target_class_compatibility(det_class: str, target_props: dict) -> tuple[float, str]:
    det_text = clean_detection_class(det_class).lower()
    target_text = " ".join(str(target_props.get(key, "")) for key in ("name", "type", "category", "description")).lower()
    if any(token in target_text for token in det_text.split() if len(token) >= 4):
        return 0.35, "class/name text overlap"
    category = conservative_detection_ontology(det_class).get("category")
    if category and category in target_text:
        return 0.3, "category overlap"
    return 0.15, "generic proximity match"


def generate_candidate_links_for_pass(pass_id: int, distance_threshold_meters: float = 1500.0) -> int:
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT d.id, d.class, d.confidence, ST_X(d.centroid) AS lon, ST_Y(d.centroid) AS lat
            FROM detections d
            WHERE d.pass_id = %s
        """, (pass_id,))
        rows = cursor.fetchall()

    try:
        with db.get_session() as session:
            result = session.run("""
                MATCH (t:Target)
                WHERE t.latitude IS NOT NULL AND t.longitude IS NOT NULL
                RETURN elementId(t) AS element_id, t.id AS stable_id, t.name AS name,
                       t.latitude AS lat, t.longitude AS lon, properties(t) AS props
            """)
            targets = [dict(record) for record in result]
    except Exception as exc:
        logger.warning("Unable to read targets for candidate links: %s", exc)
        targets = []

    created = 0
    with postgis_db.get_cursor(commit=True) as cursor:
        for det in rows:
            for target in targets:
                distance_m = target_distance_m(float(det["lat"]), float(det["lon"]), float(target["lat"]), float(target["lon"]))
                if distance_m > distance_threshold_meters:
                    continue
                compatibility, compatibility_reason = target_class_compatibility(det["class"], target.get("props") or {})
                distance_score = max(0.0, 1.0 - (distance_m / distance_threshold_meters)) * 0.45
                confidence_score = max(0.0, min(1.0, float(det["confidence"] or 0))) * 0.2
                score = round(distance_score + compatibility + confidence_score, 3)
                target_id = target.get("stable_id") or target["element_id"]
                evidence = {
                    "distance_m": round(distance_m, 2),
                    "compatibility_reason": compatibility_reason,
                    "detection_class": det["class"],
                    "detection_confidence": float(det["confidence"] or 0),
                }
                cursor.execute("""
                    INSERT INTO detection_target_candidates (detection_id, target_id, target_name, score, reason, status, evidence)
                    VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                    ON CONFLICT (detection_id, target_id) DO UPDATE SET
                        target_name = EXCLUDED.target_name,
                        score = EXCLUDED.score,
                        reason = EXCLUDED.reason,
                        evidence = EXCLUDED.evidence,
                        updated_at = NOW()
                    RETURNING id
                """, (
                    det["id"],
                    target_id,
                    target.get("name") or target_id,
                    score,
                    f"{round(distance_m)}m from target; {compatibility_reason}; confidence {float(det['confidence'] or 0):.2f}",
                    json.dumps(evidence, default=str),
                ))
                if cursor.fetchone():
                    created += 1
    return created


@celery_app.task(queue="imagery", bind=True)
def process_satellite_imagery(self, image_url: str, sensor_type: str = "Optical", acquisition_time: str = None, upload_id: str = None):
    """
    Full pipeline: download/validate -> COG conversion -> catalog -> inference -> store.
    """
    try:
        logger.info("[WORKER] Processing satellite image: %s", image_url)
        publish_event("imagery", {"type": "ingest_started", "image_url": image_url, "upload_id": upload_id})
        publish_event("ops", {"type": "imagery_ingest_started", "image_url": image_url, "upload_id": upload_id})

        # 1. Determine local path
        ensure_worker_imagery_schema()
        input_path = resolve_input_path(image_url)
        filename = os.path.basename(input_path)
        upload_job = get_upload_job(upload_id)
        original_filename = upload_job.get("filename") or filename
        raster_metadata = extract_raster_metadata(input_path)
        source_hash = raster_metadata.get("source_hash")
        source_filename = original_filename
        update_upload_job(
            upload_id=upload_id,
            file_path=input_path,
            status="processing",
            metadata={"task_id": self.request.id, "raster_metadata": raster_metadata, "source_hash": source_hash},
        )
        report_progress(self, upload_id, input_path, "processing", 10, "Resolved imagery input and metadata.")

        # 2. Convert to COG
        cog_name = f"{os.path.splitext(filename)[0]}_cog.tif"
        cog_path = os.path.join(IMAGERY_PATH, "processed", cog_name)
        os.makedirs(os.path.dirname(cog_path), exist_ok=True)

        report_progress(self, upload_id, input_path, "conversion", 20, "Converting raster to Cloud Optimized GeoTIFF.")
        ensure_cog(input_path, cog_path)
        logger.info("[WORKER] COG created: %s", cog_path)
        report_progress(self, upload_id, input_path, "conversion", 35, "COG conversion complete.", {"cog_path": cog_path})

        # 3. Extract footprint and catalog in PostGIS
        report_progress(self, upload_id, input_path, "cataloging", 45, "Extracting footprint and cataloging imagery.")
        footprint, min_lon, min_lat, max_lon, max_lat = get_raster_footprint(cog_path)
        footprint_wkt = footprint.wkt

        acq_time = acquisition_time or raster_metadata.get("acquisition_time") or datetime.now(timezone.utc).isoformat()

        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                SELECT id
                FROM satellite_passes
                WHERE acquisition_time = %s::timestamptz
                  AND (
                    (%s IS NOT NULL AND source_hash = %s)
                    OR (source_filename = %s AND ST_Equals(footprint, ST_GeomFromText(%s, 4326)))
                    OR (source_filename IS NULL AND (name = %s OR name LIKE %s) AND ST_Equals(footprint, ST_GeomFromText(%s, 4326)))
                  )
                ORDER BY updated_at DESC NULLS LAST, created_at DESC
                LIMIT 1
            """, (acq_time, source_hash, source_hash, source_filename, footprint_wkt, source_filename, f"%{source_filename}", footprint_wkt))
            existing = cursor.fetchone()
            if existing:
                pass_id = existing["id"]
                cursor.execute("""
                    UPDATE satellite_passes
                    SET name = %s,
                        file_path = %s,
                        sensor_type = %s,
                        acquisition_time = %s,
                        footprint = ST_GeomFromText(%s, 4326),
                        crs = %s,
                        metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb,
                        source_hash = %s,
                        source_filename = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                """, (
                    original_filename,
                    cog_path,
                    sensor_type,
                    acq_time,
                    footprint_wkt,
                    "EPSG:4326",
                    json.dumps({**raster_metadata, "upload_id": upload_id, "replacement": True}, default=str),
                    source_hash,
                    source_filename,
                    pass_id,
                ))
                cursor.fetchone()
                replacement = True
            else:
                cursor.execute("""
                    INSERT INTO satellite_passes (
                        name, file_path, sensor_type, acquisition_time, footprint, crs,
                        metadata, source_hash, source_filename, updated_at
                    )
                    VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s, %s, %s, %s, NOW())
                    RETURNING id
                """, (
                    original_filename,
                    cog_path,
                    sensor_type,
                    acq_time,
                    footprint_wkt,
                    "EPSG:4326",
                    json.dumps({**raster_metadata, "upload_id": upload_id, "replacement": False}, default=str),
                    source_hash,
                    source_filename,
                ))
                pass_id = cursor.fetchone()["id"]
                replacement = False

        logger.info("[WORKER] Cataloged in PostGIS with id=%s replacement=%s", pass_id, replacement)
        report_progress(
            self,
            upload_id,
            input_path,
            "classification",
            50,
            "Imagery cataloged; replacing matching timestamp detections." if replacement else "Imagery cataloged; preparing classification graph.",
            {"pass_id": pass_id, "acquisition_time": acq_time, "replacement": replacement},
        )
        record_timeline_event(
            "GEOINT",
            "imagery_replaced" if replacement else "imagery_cataloged",
            original_filename,
            {"pass_id": pass_id, "upload_id": upload_id, "source_hash": source_hash, "replacement": replacement},
            occurred_at=acq_time,
        )

        # 4. Create SatellitePass node in Neo4j
        with db.get_session() as session:
            session.run("""
                MERGE (sp:SatellitePass {postgis_id: $pass_id})
                ON CREATE SET sp.created_at = datetime()
                SET sp.name = $name,
                    sp.sensor_type = $sensor_type,
                    sp.acquisition_time = $acq_time,
                    sp.file_path = $file_path,
                    sp.min_lon = $min_lon,
                    sp.min_lat = $min_lat,
                    sp.max_lon = $max_lon,
                    sp.max_lat = $max_lat,
                    sp.updated_at = datetime()
            """, {
                "pass_id": pass_id,
                "name": original_filename,
                "sensor_type": sensor_type,
                "acq_time": acq_time,
                "file_path": cog_path,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat
            })

        # 5. Tiling inference
        report_progress(self, upload_id, input_path, "inference", 55, "Starting chip inference and classification.", {"pass_id": pass_id})
        clear_existing_detections(pass_id)
        logger.info("[WORKER] Starting tiling inference...")
        detections = slice_and_infer(
            cog_path,
            pass_id,
            progress_callback=lambda stage, progress, message, extra=None: report_progress(
                self,
                upload_id,
                input_path,
                stage,
                progress,
                message,
                {"pass_id": pass_id, **(extra or {})},
            ),
        )
        logger.info("[WORKER] Total detections after dedupe: %s", len(detections))
        report_progress(self, upload_id, input_path, "llm_classification", 90, "Inference complete; queueing LLM detection classification.", {"pass_id": pass_id, "detections_count": len(detections)})
        ontology_by_class = classify_detection_ontologies(
            detections,
            progress_callback=lambda stage, progress, message, extra=None: report_progress(
                self,
                upload_id,
                input_path,
                stage,
                progress,
                message,
                {"pass_id": pass_id, "detections_count": len(detections), **(extra or {})},
            ),
        )

        # 6. Store detections
        report_progress(self, upload_id, input_path, "storage", 95, "Storing detections and generating candidate links.", {"pass_id": pass_id})
        stored_count = store_detections(detections, pass_id, ontology_by_class)
        candidate_count = generate_candidate_links_for_pass(pass_id)
        logger.info("[WORKER] Stored %s detections and generated %s candidate links.", stored_count, candidate_count)

        payload = {
            "pass_id": pass_id,
            "cog_path": cog_path,
            "upload_id": upload_id,
            "detections_count": stored_count,
            "candidate_links_count": candidate_count,
            "acquisition_time": acq_time,
            "replacement": replacement,
        }
        update_upload_job(upload_id=upload_id, file_path=input_path, status="ready", metadata={
            **payload,
            "stage": "ready",
            "progress": 100,
            "message": "Imagery processing complete.",
        })
        publish_event("detections", {"type": "detections_updated", **payload})
        publish_event("imagery", {"type": "ingest_succeeded", "stage": "ready", "progress": 100, **payload})
        publish_event("ops", {"type": "imagery_ready", "stage": "ready", "progress": 100, **payload})

        return payload
    except Exception as e:
        logger.exception("[WORKER] Imagery ingest failed: %s", e)
        failed_path = locals().get("input_path") or image_url
        update_upload_job(
            upload_id=upload_id,
            file_path=failed_path,
            status="failed",
            metadata={
                "error": str(e),
                "task_id": self.request.id,
                "stage": "failed",
                "message": f"Imagery processing failed: {e}",
            },
        )
        publish_event("imagery", {"type": "ingest_failed", "image_url": image_url, "upload_id": upload_id, "error": str(e)})
        publish_event("ops", {"type": "imagery_failed", "image_url": image_url, "upload_id": upload_id, "error": str(e)})
        raise
