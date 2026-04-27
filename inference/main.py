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
def resolve_model_path() -> str:
    candidates = [
        os.getenv("MODEL_PATH"),
        os.getenv("TRAINED_MODEL_PATH"),
        "/app/models/geoint_yolov8_obb.pt",
        "models/geoint_yolov8_obb.pt",
        "/app/models/geoint_yolov8.pt",
        "models/geoint_yolov8.pt",
        "/app/yolov8n.pt",
        "yolov8n.pt",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return os.getenv("MODEL_PATH") or os.getenv("TRAINED_MODEL_PATH") or "/app/models/geoint_yolov8.pt"


MODEL_PATH = resolve_model_path()
MODEL_TASK = os.getenv("MODEL_TASK") or ("obb" if "obb" in os.path.basename(MODEL_PATH).lower() else "detect")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.25"))


def resolve_device() -> str:
    requested = os.getenv("DEVICE", "auto").strip().lower()
    if requested and requested != "auto":
        return requested
    try:
        import torch
    except ImportError:
        print("[INFERENCE] WARNING: torch is unavailable; falling back to CPU.")
        return "cpu"

    cuda_version = getattr(torch.version, "cuda", None)
    if torch.cuda.is_available():
        device_name = torch.cuda.get_device_name(0)
        print(f"[INFERENCE] Using CUDA device 0: {device_name} (torch CUDA {cuda_version})")
        return "cuda:0"

    print(
        "[INFERENCE] WARNING: PyTorch reports CUDA is unavailable; using CPU. "
        f"torch={torch.__version__}, torch CUDA={cuda_version}"
    )
    return "cpu"


DEVICE = resolve_device()

detection_model = None

def load_model():
    global detection_model
    if MODEL_TASK == "obb":
        if YOLO_AVAILABLE and os.path.exists(MODEL_PATH):
            detection_model = YOLO(MODEL_PATH)
            try:
                detection_model.to(DEVICE)
            except Exception as exc:
                print(f"[INFERENCE] WARNING: unable to move YOLO OBB model to {DEVICE}: {exc}")
            print(f"[INFERENCE] YOLO OBB model loaded from {MODEL_PATH} on {DEVICE}")
        else:
            print(f"[INFERENCE] WARNING: OBB model file does not exist or ultralytics is unavailable: {MODEL_PATH}")
    elif SAHI_AVAILABLE:
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
            if YOLO_AVAILABLE and os.path.exists(MODEL_PATH):
                try:
                    detection_model = YOLO(MODEL_PATH)
                    detection_model.to(DEVICE)
                except Exception as yolo_error:
                    print(f"[INFERENCE] Plain YOLOv8 fallback failed: {yolo_error}")
    elif YOLO_AVAILABLE:
        if os.path.exists(MODEL_PATH):
            detection_model = YOLO(MODEL_PATH)
            try:
                detection_model.to(DEVICE)
            except Exception as exc:
                print(f"[INFERENCE] WARNING: unable to move YOLO model to {DEVICE}: {exc}")
            print(f"[INFERENCE] Plain YOLOv8 model loaded from {MODEL_PATH} on {DEVICE}")
        else:
            print(f"[INFERENCE] WARNING: Model file does not exist: {MODEL_PATH}")
    if detection_model is None:
        print("[INFERENCE] WARNING: No detection model available. /detect will return 503.")

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
            results = detection_model(img_array, device=DEVICE, verbose=False)
            for r in results:
                img_w, img_h = image.size
                obb = getattr(r, "obb", None)
                if obb is not None and getattr(obb, "xyxyxyxy", None) is not None:
                    points_batch = obb.xyxyxyxy.cpu().numpy()
                    classes = obb.cls.cpu().numpy() if obb.cls is not None else []
                    confidences = obb.conf.cpu().numpy() if obb.conf is not None else []
                    for index, points in enumerate(points_batch):
                        flat = points.reshape(-1).tolist()
                        xs = flat[0::2]
                        ys = flat[1::2]
                        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
                        cx = (x1 + x2) / 2 / img_w
                        cy = (y1 + y2) / 2 / img_h
                        w = (x2 - x1) / img_w
                        h = (y2 - y1) / img_h
                        cls_id = int(classes[index]) if len(classes) > index else 0
                        cls_name = detection_model.names.get(cls_id, f"class_{cls_id}")
                        detections.append({
                            "class": cls_name,
                            "bbox": [cx, cy, w, h],
                            "obb": [
                                max(0.0, min(1.0, flat[i] / (img_w if i % 2 == 0 else img_h)))
                                for i in range(8)
                            ],
                            "confidence": float(confidences[index]) if len(confidences) > index else 0.0,
                        })
                    continue

                boxes = r.boxes
                if boxes is None:
                    continue
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    
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
        raise HTTPException(status_code=503, detail="No detection model is loaded; refusing to fabricate detections.")
    
    processing_time = time.time() - start_time
    
    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": round(processing_time * 1000, 2),
        "model": MODEL_PATH,
        "task": MODEL_TASK,
        "device": DEVICE
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
        "model_task": MODEL_TASK,
        "model_exists": os.path.exists(MODEL_PATH),
        "device": DEVICE,
        "sahi_available": SAHI_AVAILABLE,
        "yolo_available": YOLO_AVAILABLE
    }
