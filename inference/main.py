import os
import io
import time
import json
from typing import List, Optional
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from pydantic import BaseModel
import numpy as np
from PIL import Image

# SAHI + Ultralytics imports
try:
    from sahi import AutoDetectionModel
    from sahi.predict import get_sliced_prediction
    SAHI_AVAILABLE = True
except ImportError:
    SAHI_AVAILABLE = False

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

app = FastAPI(title="Magritte AIP Node - Satellite Imagery Inference")

# Configuration
MODEL_PATH = os.getenv("MODEL_PATH", "yolov8n.pt")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.25"))
DEVICE = os.getenv("DEVICE", "cuda:0")

detection_model = None

def load_model():
    global detection_model
    if SAHI_AVAILABLE:
        try:
            detection_model = AutoDetectionModel.from_pretrained(
                model_type='yolov8',
                model_path=MODEL_PATH,
                confidence_threshold=CONFIDENCE_THRESHOLD,
                device=DEVICE,
            )
            print(f"[INFERENCE] SAHI + YOLOv8 model loaded from {MODEL_PATH} on {DEVICE}")
        except Exception as e:
            print(f"[INFERENCE] SAHI load failed: {e}. Falling back to plain YOLOv8.")
            if YOLO_AVAILABLE:
                detection_model = YOLO(MODEL_PATH)
    elif YOLO_AVAILABLE:
        detection_model = YOLO(MODEL_PATH)
        print(f"[INFERENCE] Plain YOLOv8 model loaded from {MODEL_PATH}")
    else:
        print("[INFERENCE] WARNING: No detection model available. Using mock mode.")

def run_inference(image: Image.Image, metadata: dict = None):
    """
    Run detection on a single image chip.
    Returns list of detections with normalized bbox [x_center, y_center, width, height].
    """
    start_time = time.time()
    detections = []
    
    # Convert PIL to numpy array
    img_array = np.array(image)
    
    if detection_model is None:
        load_model()
    
    if SAHI_AVAILABLE and hasattr(detection_model, 'perform_inference'):
        # SAHI mode
        try:
            result = get_sliced_prediction(
                img_array,
                detection_model,
                slice_height=640,
                slice_width=640,
                overlap_height_ratio=0.2,
                overlap_width_ratio=0.2,
                postprocess_type="NMS",
                postprocess_match_threshold=0.5,
                verbose=0
            )
            
            for obj in result.object_prediction_list:
                bbox = obj.bbox  # sahi.prediction.BoundingBox
                # Convert to normalized [x_center, y_center, width, height]
                x1, y1, x2, y2 = bbox.minx, bbox.miny, bbox.maxx, bbox.maxy
                img_w, img_h = image.size
                
                cx = (x1 + x2) / 2 / img_w
                cy = (y1 + y2) / 2 / img_h
                w = (x2 - x1) / img_w
                h = (y2 - y1) / img_h
                
                detections.append({
                    "class": obj.category.name if hasattr(obj.category, 'name') else str(obj.category.id),
                    "bbox": [cx, cy, w, h],
                    "confidence": float(obj.score.value)
                })
        except Exception as e:
            print(f"[INFERENCE] SAHI inference error: {e}")
    
    elif YOLO_AVAILABLE and detection_model is not None:
        # Plain YOLOv8 mode
        try:
            results = detection_model(img_array, verbose=False)
            for r in results:
                boxes = r.boxes
                if boxes is None:
                    continue
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    img_w, img_h = image.size
                    
                    cx = (x1 + x2) / 2 / img_w
                    cy = (y1 + y2) / 2 / img_h
                    w = (x2 - x1) / img_w
                    h = (y2 - y1) / img_h
                    
                    cls_id = int(box.cls[0])
                    cls_name = detection_model.names.get(cls_id, f"class_{cls_id}")
                    
                    detections.append({
                        "class": cls_name,
                        "bbox": [cx, cy, w, h],
                        "confidence": float(box.conf[0])
                    })
        except Exception as e:
            print(f"[INFERENCE] YOLO inference error: {e}")
    
    else:
        # Mock mode for testing without model
        import random
        num_detections = random.randint(0, 2)
        classes = ["Vessel", "Aircraft", "Facility"]
        for _ in range(num_detections):
            det_class = random.choice(classes)
            bbox = [
                random.uniform(0.2, 0.8),
                random.uniform(0.2, 0.8),
                random.uniform(0.05, 0.3),
                random.uniform(0.05, 0.3)
            ]
            confidence = random.uniform(0.7, 0.99)
            detections.append({
                "class": det_class,
                "bbox": bbox,
                "confidence": confidence
            })
    
    processing_time = time.time() - start_time
    
    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": round(processing_time * 1000, 2),
        "model": MODEL_PATH,
        "device": DEVICE if detection_model else "cpu_mock"
    }


@app.on_event("startup")
def startup_event():
    load_model()


@app.post("/detect")
async def detect_objects(
    image: UploadFile = File(...),
    metadata: str = Form("{}")
):
    """
    Accept an image file and return detections.
    Supports PNG, JPG, TIFF chips.
    """
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        meta = {}
    
    # Read image
    contents = await image.read()
    try:
        pil_image = Image.open(io.BytesIO(contents))
        # Ensure RGB
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image file: {e}")
    
    result = run_inference(pil_image, meta)
    result["input_metadata"] = meta
    
    return result


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": detection_model is not None,
        "model_path": MODEL_PATH,
        "device": DEVICE,
        "sahi_available": SAHI_AVAILABLE,
        "yolo_available": YOLO_AVAILABLE
    }
