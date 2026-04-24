"""
Seed PostGIS with sample satellite pass data and detections for demonstration.
Run this after PostGIS is initialized: docker exec -it osint-backend-1 python seed_postgis.py
"""
import os
from database import postgis_db
from datetime import datetime, timedelta
import random

def seed_postgis():
    print("Seeding PostGIS with sample satellite data...")
    
    with postgis_db.get_cursor(commit=True) as cursor:
        # Insert sample satellite passes
        passes = [
            {
                "name": "USGS_Pass_001.tif",
                "file_path": "/data/imagery/processed/usgs_pass_001_cog.tif",
                "sensor_type": "Optical",
                "acquisition_time": datetime.utcnow() - timedelta(hours=2),
                "cloud_cover": 12.5,
                "footprint": "MULTIPOLYGON(((54.0 24.0, 54.0 26.0, 56.0 26.0, 56.0 24.0, 54.0 24.0)))"
            },
            {
                "name": "NASA_Worldview_042.tif",
                "file_path": "/data/imagery/processed/nasa_worldview_042_cog.tif",
                "sensor_type": "Optical",
                "acquisition_time": datetime.utcnow() - timedelta(hours=6),
                "cloud_cover": 5.0,
                "footprint": "MULTIPOLYGON(((54.5 24.5, 54.5 25.5, 55.5 25.5, 55.5 24.5, 54.5 24.5)))"
            },
            {
                "name": "SAR_Collection_007.tif",
                "file_path": "/data/imagery/processed/sar_collection_007_cog.tif",
                "sensor_type": "Radar",
                "acquisition_time": datetime.utcnow() - timedelta(hours=12),
                "cloud_cover": 0.0,
                "footprint": "MULTIPOLYGON(((55.0 25.0, 55.0 26.0, 56.0 26.0, 56.0 25.0, 55.0 25.0)))"
            }
        ]
        
        pass_ids = []
        for p in passes:
            cursor.execute("""
                INSERT INTO satellite_passes (name, file_path, sensor_type, acquisition_time, cloud_cover, footprint)
                VALUES (%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                ON CONFLICT (file_path) DO NOTHING
                RETURNING id
            """, (p["name"], p["file_path"], p["sensor_type"], p["acquisition_time"], p["cloud_cover"], p["footprint"]))
            
            result = cursor.fetchone()
            if result:
                pass_ids.append(result["id"])
            else:
                # Get existing id
                cursor.execute("SELECT id FROM satellite_passes WHERE file_path = %s", (p["file_path"],))
                pass_ids.append(cursor.fetchone()["id"])
        
        # Insert sample detections
        classes = ["Vessel", "Aircraft", "Facility"]
        for i, pass_id in enumerate(pass_ids):
            for j in range(random.randint(2, 5)):
                det_class = random.choice(classes)
                confidence = random.uniform(0.75, 0.99)
                
                # Random lat/lon within the pass footprint
                lat = random.uniform(24.2, 25.8)
                lon = random.uniform(54.2, 55.8)
                
                # Small bounding box around the point
                delta = 0.005
                geom_wkt = f"POLYGON(({lon-delta} {lat-delta}, {lon-delta} {lat+delta}, {lon+delta} {lat+delta}, {lon+delta} {lat-delta}, {lon-delta} {lat-delta}))"
                centroid_wkt = f"POINT({lon} {lat})"
                
                cursor.execute("""
                    INSERT INTO detections (pass_id, class, confidence, geom, centroid, pixel_bbox, metadata)
                    VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s, %s)
                """, (
                    pass_id,
                    det_class,
                    confidence,
                    geom_wkt,
                    centroid_wkt,
                    {"bbox": [random.randint(0, 1000), random.randint(0, 1000), 640, 640]},
                    {"source": "seed", "chip_size": 640}
                ))
        
        print(f"Inserted {len(passes)} satellite passes and sample detections into PostGIS.")

if __name__ == "__main__":
    seed_postgis()
