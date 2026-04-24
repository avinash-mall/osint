import os
import requests
import subprocess
import uuid
from datetime import datetime
from celery import Celery
from database import db, postgis_db
import rasterio
from rasterio.windows import Window
from rasterio.transform import xy
from shapely.geometry import Polygon, mapping
import numpy as np

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://localhost:8001")
IMAGERY_PATH = os.getenv("IMAGERY_PATH", "/data/imagery")

celery_app = Celery("gotham_worker", broker=REDIS_URL)

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
            if "band" in ds.dims and ds.dims["band"] > 1:
                ds = ds.sel(band=1)
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
        
        footprint = Polygon([
            (min_lon, min_lat),
            (min_lon, max_lat),
            (max_lon, max_lat),
            (max_lon, min_lat),
            (min_lon, min_lat)
        ])
        return footprint, min_lon, min_lat, max_lon, max_lat


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
                if np.all(chip == 0) or np.all(chip == src.nodata):
                    continue
                
                # Save temporary chip image
                chip_filename = f"chip_{pass_id}_{x}_{y}.png"
                chip_path = os.path.join(IMAGERY_PATH, "chips", chip_filename)
                os.makedirs(os.path.dirname(chip_path), exist_ok=True)
                
                # Write chip as PNG for inference service
                profile = src.profile.copy()
                profile.update({
                    "driver": "PNG",
                    "height": win_height,
                    "width": win_width,
                    "transform": rasterio.windows.transform(window, transform)
                })
                with rasterio.open(chip_path, "w", **profile) as dst:
                    dst.write(chip)
                
                # Inference
                try:
                    with open(chip_path, "rb") as f:
                        resp = requests.post(
                            f"{INFERENCE_URL}/detect",
                            files={"image": f},
                            data={"metadata": f'{{"pass_id": {pass_id}, "window": [{x}, {y}, {win_width}, {win_height}]}}'},
                            timeout=60
                        )
                    if resp.status_code == 200:
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
                    print(f"Inference error for chip {chip_path}: {e}")
                finally:
                    # Clean up chip file
                    if os.path.exists(chip_path):
                        os.remove(chip_path)
    
    return detections


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
                {"bbox": pixel_bbox},
                {"source": "inference", "chip_size": 640}
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


@celery_app.task(queue="imagery")
def process_satellite_imagery(image_url: str, sensor_type: str = "Optical", acquisition_time: str = None):
    """
    Full pipeline: download/validate -> COG conversion -> catalog -> inference -> store.
    """
    print(f"[WORKER] Processing satellite image: {image_url}")
    
    # 1. Determine local path
    filename = os.path.basename(image_url)
    input_path = os.path.join(IMAGERY_PATH, "incoming", filename)
    os.makedirs(os.path.dirname(input_path), exist_ok=True)
    
    # If URL is local path, use directly
    if image_url.startswith("/") or image_url.startswith("s3://") == False:
        if not os.path.exists(input_path) and os.path.exists(image_url):
            input_path = image_url
    
    # 2. Convert to COG
    cog_name = f"{os.path.splitext(filename)[0]}_cog.tif"
    cog_path = os.path.join(IMAGERY_PATH, "processed", cog_name)
    os.makedirs(os.path.dirname(cog_path), exist_ok=True)
    
    try:
        ensure_cog(input_path, cog_path)
        print(f"[WORKER] COG created: {cog_path}")
    except Exception as e:
        print(f"[WORKER] COG conversion failed: {e}")
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
    
    print(f"[WORKER] Cataloged in PostGIS with id={pass_id}")
    
    # 4. Create SatellitePass node in Neo4j
    with db.get_session() as session:
        session.run("""
            CREATE (sp:SatellitePass {
                postgis_id: $pass_id,
                name: $name,
                sensor_type: $sensor_type,
                acquisition_time: $acq_time,
                file_path: $file_path,
                min_lon: $min_lon,
                min_lat: $min_lat,
                max_lon: $max_lon,
                max_lat: $max_lat,
                created_at: datetime()
            })
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
    print(f"[WORKER] Starting tiling inference...")
    detections = slice_and_infer(cog_path, pass_id)
    print(f"[WORKER] Total detections: {len(detections)}")
    
    # 6. Store detections
    stored_count = store_detections(detections, pass_id)
    print(f"[WORKER] Stored {stored_count} detections.")
    
    return {
        "pass_id": pass_id,
        "cog_path": cog_path,
        "detections_count": stored_count
    }
