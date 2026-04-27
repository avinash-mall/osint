import os
import json
import requests
import subprocess
import uuid
import logging
from datetime import datetime
from urllib.parse import urlparse
from celery import Celery
from database import db, postgis_db
import rasterio
from rasterio.windows import Window
from rasterio.transform import xy
from shapely.geometry import Polygon, MultiPolygon
import numpy as np
from PIL import Image

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://localhost:8001")
IMAGERY_PATH = os.getenv("IMAGERY_PATH", "/data/imagery")

logger = logging.getLogger(__name__)

celery_app = Celery("sentinelos_worker", broker=REDIS_URL, backend=REDIS_URL)


def publish_event(topic: str, payload: dict) -> None:
    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True)
        client.publish(f"events:{topic}", json.dumps(payload, default=str))
        client.close()
    except Exception as e:
        logger.warning("Failed to publish %s event: %s", topic, e)

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


def slice_and_infer(cog_path: str, pass_id: int, chip_size: int = 640, overlap: int = 100):
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
        for y in range(0, height, step):
            for x in range(0, width, step):
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

                        # Convert pixel coords to geo coords
                        lon1, lat1 = xy(transform, abs_px_y1, abs_px_x1, offset="center")
                        lon2, lat2 = xy(transform, abs_px_y2, abs_px_x2, offset="center")

                        # Reproject to EPSG:4326 if needed
                        if crs and crs.to_string() != "EPSG:4326":
                            from rasterio.warp import transform as rasterio_transform
                            (lon1, lon2), (lat1, lat2) = rasterio_transform(
                                crs, "EPSG:4326", [lon1, lon2], [lat1, lat2]
                            )

                        det["pixel_bbox"] = [abs_px_x1, abs_px_y1, abs_px_x2, abs_px_y2]
                        det["geo_bbox"] = [lon1, lat1, lon2, lat2]
                        detections.append(det)
                    
                except Exception as e:
                    raise RuntimeError(f"Inference failed for chip {chip_path}: {e}") from e
                finally:
                    # Clean up chip file
                    if os.path.exists(chip_path):
                        os.remove(chip_path)
    
    return deduplicate_detections(detections)


def store_detections(detections: list, pass_id: int):
    """Store detections in PostGIS and create Neo4j nodes."""
    if not detections:
        return 0
    
    with postgis_db.get_cursor(commit=True) as cursor:
        for det in detections:
            lon1, lat1, lon2, lat2 = det["geo_bbox"]
            confidence = det.get("confidence", 0.0)
            det_class = det.get("class", "Unknown")
            pixel_bbox = det.get("pixel_bbox", [])
            
            # Create WKT polygons
            geom_wkt = f"POLYGON(({lon1} {lat1}, {lon1} {lat2}, {lon2} {lat2}, {lon2} {lat1}, {lon1} {lat1}))"
            centroid_wkt = f"POINT({(lon1+lon2)/2} {(lat1+lat2)/2})"
            
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
                json.dumps({"bbox": pixel_bbox}),
                json.dumps({"source": "inference", "chip_size": 640})
            ))
            
            det_id = cursor.fetchone()["id"]
            
            # Insert into Neo4j
            with db.get_session() as neo_session:
                neo_session.run("""
                    MATCH (sp:SatellitePass {postgis_id: $pass_id})
                    CREATE (d:Detection {
                        postgis_id: $det_id,
                        class: $det_class,
                        confidence: $confidence,
                        latitude: $lat,
                        longitude: $lon,
                        created_at: datetime()
                    })
                    CREATE (sp)-[:CONTAINS_DETECTION]->(d)
                """, {
                    "pass_id": pass_id,
                    "det_id": det_id,
                    "det_class": det_class,
                    "confidence": confidence,
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


def resolve_detections_for_pass(pass_id: int, distance_threshold_meters: float = 500.0) -> int:
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT d.id, d.class, d.confidence, ST_X(d.centroid) AS lon, ST_Y(d.centroid) AS lat
            FROM detections d
            WHERE d.pass_id = %s
        """, (pass_id,))
        rows = cursor.fetchall()

    resolved = 0
    with db.get_session() as session:
        for det in rows:
            result = session.run("""
                MATCH (t:Target)
                WHERE t.latitude IS NOT NULL AND t.longitude IS NOT NULL
                  AND point.distance(
                      point({latitude: t.latitude, longitude: t.longitude}),
                      point({latitude: $lat, longitude: $lon})
                  ) < $threshold
                RETURN t
                ORDER BY point.distance(
                    point({latitude: t.latitude, longitude: t.longitude}),
                    point({latitude: $lat, longitude: $lon})
                ) ASC
                LIMIT 1
            """, {"lat": det["lat"], "lon": det["lon"], "threshold": distance_threshold_meters})
            existing = result.single()
            if existing:
                session.run("""
                    MATCH (t:Target) WHERE elementId(t) = $target_id
                    MATCH (d:Detection {postgis_id: $det_id})
                    MERGE (t)-[:DETECTED_AS]->(d)
                """, {"target_id": existing["t"].element_id, "det_id": det["id"]})
            else:
                target_id = str(uuid.uuid4())
                session.run("""
                    MATCH (d:Detection {postgis_id: $det_id})
                    CREATE (t:Target {
                        id: $target_id,
                        name: $name,
                        priority: 'Medium',
                        status: 'Active',
                        description: $description,
                        latitude: $lat,
                        longitude: $lon,
                        confidence: $confidence,
                        detection_id: $det_id
                    })
                    MERGE (t)-[:DETECTED_AS]->(d)
                """, {
                    "det_id": det["id"],
                    "target_id": target_id,
                    "name": f"Unresolved {det['class']} {target_id[:6]}",
                    "description": f"Automated entity resolution for {det['class']} detection.",
                    "lat": det["lat"],
                    "lon": det["lon"],
                    "confidence": det["confidence"],
                })
            resolved += 1
    return resolved


@celery_app.task(queue="imagery")
def process_satellite_imagery(image_url: str, sensor_type: str = "Optical", acquisition_time: str = None):
    """
    Full pipeline: download/validate -> COG conversion -> catalog -> inference -> store.
    """
    logger.info("[WORKER] Processing satellite image: %s", image_url)
    publish_event("imagery", {"type": "ingest_started", "image_url": image_url})
    
    # 1. Determine local path
    input_path = resolve_input_path(image_url)
    filename = os.path.basename(input_path)
    
    # 2. Convert to COG
    cog_name = f"{os.path.splitext(filename)[0]}_cog.tif"
    cog_path = os.path.join(IMAGERY_PATH, "processed", cog_name)
    os.makedirs(os.path.dirname(cog_path), exist_ok=True)
    
    try:
        ensure_cog(input_path, cog_path)
        logger.info("[WORKER] COG created: %s", cog_path)
    except Exception as e:
        logger.exception("[WORKER] COG conversion failed: %s", e)
        publish_event("imagery", {"type": "ingest_failed", "image_url": image_url, "error": str(e)})
        raise
    
    # 3. Extract footprint and catalog in PostGIS
    footprint, min_lon, min_lat, max_lon, max_lat = get_raster_footprint(cog_path)
    footprint_wkt = footprint.wkt
    
    acq_time = acquisition_time or datetime.utcnow().isoformat()
    
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO satellite_passes (name, file_path, sensor_type, acquisition_time, footprint, crs)
            VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s)
            ON CONFLICT (file_path) DO UPDATE SET
                name = EXCLUDED.name,
                sensor_type = EXCLUDED.sensor_type,
                acquisition_time = EXCLUDED.acquisition_time,
                footprint = EXCLUDED.footprint
            RETURNING id
        """, (filename, cog_path, sensor_type, acq_time, footprint_wkt, "EPSG:4326"))
        
        pass_id = cursor.fetchone()["id"]
    
    logger.info("[WORKER] Cataloged in PostGIS with id=%s", pass_id)
    
    # 4. Create SatellitePass node in Neo4j
    with db.get_session() as session:
        session.run("""
            MERGE (sp:SatellitePass {postgis_id: $pass_id})
            SET sp.name = $name,
                sp.sensor_type = $sensor_type,
                sp.acquisition_time = $acq_time,
                sp.file_path = $file_path,
                sp.min_lon = $min_lon,
                sp.min_lat = $min_lat,
                sp.max_lon = $max_lon,
                sp.max_lat = $max_lat,
                sp.updated_at = datetime()
            ON CREATE SET sp.created_at = datetime()
        """, {
            "pass_id": pass_id,
            "name": filename,
            "sensor_type": sensor_type,
            "acq_time": acq_time,
            "file_path": cog_path,
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat
        })
    
    # 5. Tiling inference
    clear_existing_detections(pass_id)
    logger.info("[WORKER] Starting tiling inference...")
    detections = slice_and_infer(cog_path, pass_id)
    logger.info("[WORKER] Total detections after dedupe: %s", len(detections))
    
    # 6. Store detections
    stored_count = store_detections(detections, pass_id)
    resolved_count = resolve_detections_for_pass(pass_id)
    logger.info("[WORKER] Stored %s detections and resolved %s.", stored_count, resolved_count)
    publish_event("detections", {
        "type": "detections_updated",
        "pass_id": pass_id,
        "detections_count": stored_count,
        "resolved_count": resolved_count,
    })
    publish_event("imagery", {"type": "ingest_succeeded", "pass_id": pass_id, "cog_path": cog_path})
    
    return {
        "pass_id": pass_id,
        "cog_path": cog_path,
        "detections_count": stored_count,
        "resolved_count": resolved_count
    }
