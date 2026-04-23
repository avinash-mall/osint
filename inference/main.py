from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import random
import time

app = FastAPI(title="Magritte AIP Node")

class ImageSlice(BaseModel):
    image_url: str
    metadata: dict

@app.post("/detect")
def detect_objects(slice: ImageSlice):
    # Simulate processing time
    time.sleep(1)
    
    # Generate mock detections
    num_detections = random.randint(0, 3)
    detections = []
    
    classes = ["Vessel", "Aircraft", "Facility"]
    
    for _ in range(num_detections):
        det_class = random.choice(classes)
        # Mock normalized bounding box [x_center, y_center, width, height]
        bbox = [random.uniform(0.1, 0.9), random.uniform(0.1, 0.9), random.uniform(0.05, 0.2), random.uniform(0.05, 0.2)]
        confidence = random.uniform(0.7, 0.99)
        
        detections.append({
            "class": det_class,
            "bbox": bbox,
            "confidence": confidence
        })
        
    return {
        "status": "success",
        "detections": detections,
        "processed_image_url": slice.image_url
    }

@app.get("/health")
def health():
    return {"status": "ok"}
