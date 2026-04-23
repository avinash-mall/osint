import os
import requests
from celery import Celery
from database import db
import uuid
import random

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://localhost:8001")

celery_app = Celery("gotham_worker", broker=REDIS_URL)

@celery_app.task
def process_satellite_imagery(image_url: str):
    print(f"Processing satellite image: {image_url}")
    
    # 1. Simulate image slicing
    slices = [{"url": f"{image_url}_slice_{i}", "meta": {}} for i in range(4)]
    
    total_detections = []
    
    # 2. Inference
    for s in slices:
        try:
            resp = requests.post(f"{INFERENCE_URL}/detect", json={"image_url": s["url"], "metadata": s["meta"]})
            if resp.status_code == 200:
                total_detections.extend(resp.json().get("detections", []))
        except Exception as e:
            print(f"Error connecting to inference node: {e}")
            
    print(f"Total detections from image: {len(total_detections)}")
    
    # 3. Entity Resolution & Ontology Projection
    with db.get_session() as session:
        for det in total_detections:
            # Simulate projecting bounding box to Lat/Lon
            lat = random.uniform(20.0, 30.0)
            lon = random.uniform(50.0, 60.0)
            det_class = det["class"]
            
            # Simple Entity Resolution: Create a new Unknown Contact Target
            # In a real system, we'd query for assets near this lat/lon.
            
            # For demonstration, we'll create a Target node for each detection
            target_id = str(uuid.uuid4())
            target_name = f"Unknown {det_class} #{target_id[:6]}"
            
            session.run("""
            CREATE (t:Target {
                id: $id,
                name: $name,
                priority: 'High',
                status: 'Active',
                description: 'Automated detection via CV pipeline. Class: ' + $det_class,
                latitude: $lat,
                longitude: $lon
            })
            """, {"id": target_id, "name": target_name, "det_class": det_class, "lat": lat, "lon": lon})
            
    print("Entity resolution and ontology injection complete.")
    return len(total_detections)
