import os
import io
import time
import json
import threading
from typing import List, Optional
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from starlette.concurrency import run_in_threadpool
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
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.4"))
NMS_IOU_THRESHOLD = float(os.getenv("NMS_IOU_THRESHOLD", "0.5"))
MAX_DETECTIONS_PER_CHIP = int(os.getenv("MAX_DETECTIONS_PER_CHIP", "300"))


def _load_per_class_thresholds() -> dict[str, float]:
    raw = os.getenv("PER_CLASS_CONFIDENCE_OVERRIDES", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): float(v) for k, v in parsed.items()}
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"[INFERENCE] WARNING: invalid PER_CLASS_CONFIDENCE_OVERRIDES; ignoring: {exc}")
    return {}


PER_CLASS_CONFIDENCE = _load_per_class_thresholds()


def available_cpu_count() -> int:
    if hasattr(os, "sched_getaffinity"):
        try:
            return len(os.sched_getaffinity(0))
        except OSError:
            pass
    return os.cpu_count() or 1


def normalize_device_list(value: str) -> list[str]:
    devices = []
    for item in value.split(","):
        device = item.strip()
        if not device:
            continue
        if device.isdigit():
            device = f"cuda:{device}"
        devices.append(device)
    return devices or ["cpu"]


def resolve_devices() -> list[str]:
    requested = os.getenv("DEVICE", "auto").strip()
    if requested and requested.lower() != "auto":
        devices = normalize_device_list(requested)
        print(f"[INFERENCE] Using requested devices: {', '.join(devices)}")
        return devices
    try:
        import torch
    except ImportError:
        print("[INFERENCE] WARNING: torch is unavailable; falling back to CPU.")
        return ["cpu"]

    cuda_version = getattr(torch.version, "cuda", None)
    if torch.cuda.is_available():
        devices = [f"cuda:{index}" for index in range(torch.cuda.device_count())]
        names = [torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())]
        print(f"[INFERENCE] Using CUDA devices {', '.join(devices)}: {', '.join(names)} (torch CUDA {cuda_version})")
        return devices

    print(
        "[INFERENCE] WARNING: PyTorch reports CUDA is unavailable; using CPU. "
        f"torch={torch.__version__}, torch CUDA={cuda_version}"
    )
    return ["cpu"]


DEVICES = resolve_devices()
DEVICE = ",".join(DEVICES)


def configure_cpu_threads() -> int:
    requested = os.getenv("CPU_THREADS", "auto").strip().lower()
    if requested not in {"", "auto"}:
        threads = max(1, int(requested))
    elif all(device == "cpu" for device in DEVICES):
        workers = max(1, int(os.getenv("WEB_CONCURRENCY", "1")))
        threads = max(1, available_cpu_count() // workers)
    else:
        threads = max(1, min(8, available_cpu_count() // max(1, len(DEVICES))))

    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    try:
        import torch
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(max(1, min(4, threads // 2)))
    except (ImportError, RuntimeError):
        pass
    print(f"[INFERENCE] Using {threads} CPU compute threads per process.")
    return threads


CPU_THREADS = configure_cpu_threads()

detection_model = None
detection_models = []
model_pool_lock = threading.Lock()
model_pool_index = 0


def load_yolo_model(device: str):
    model = YOLO(MODEL_PATH)
    try:
        model.to(device)
    except Exception as exc:
        print(f"[INFERENCE] WARNING: unable to move YOLO model to {device}: {exc}")
    return model


def model_entry(model, device: str, kind: str) -> dict:
    return {"model": model, "device": device, "kind": kind, "lock": threading.Lock()}

def load_model():
    global detection_model, detection_models
    if detection_models:
        return
    if not os.path.exists(MODEL_PATH):
        print(f"[INFERENCE] WARNING: Model file does not exist: {MODEL_PATH}")
        return

    loaded = []
    for device in DEVICES:
        if MODEL_TASK == "obb":
            if YOLO_AVAILABLE:
                try:
                    loaded.append(model_entry(load_yolo_model(device), device, "yolo"))
                    print(f"[INFERENCE] YOLO OBB model loaded from {MODEL_PATH} on {device}")
                except Exception as exc:
                    print(f"[INFERENCE] YOLO OBB load failed on {device}: {exc}")
            else:
                print(f"[INFERENCE] WARNING: ultralytics is unavailable for OBB model: {MODEL_PATH}")
        elif SAHI_AVAILABLE:
            try:
                loaded.append(model_entry(AutoDetectionModel.from_pretrained(
                    model_type='yolov8',
                    model_path=MODEL_PATH,
                    confidence_threshold=CONFIDENCE_THRESHOLD,
                    device=device,
                ), device, "sahi"))
                print(f"[INFERENCE] SAHI + YOLOv8 model loaded from {MODEL_PATH} on {device}")
            except Exception as exc:
                print(f"[INFERENCE] SAHI load failed on {device}: {exc}. Falling back to plain YOLOv8.")
                if YOLO_AVAILABLE:
                    try:
                        loaded.append(model_entry(load_yolo_model(device), device, "yolo"))
                    except Exception as yolo_error:
                        print(f"[INFERENCE] Plain YOLOv8 fallback failed on {device}: {yolo_error}")
        elif YOLO_AVAILABLE:
            try:
                loaded.append(model_entry(load_yolo_model(device), device, "yolo"))
                print(f"[INFERENCE] Plain YOLOv8 model loaded from {MODEL_PATH} on {device}")
            except Exception as exc:
                print(f"[INFERENCE] Plain YOLOv8 load failed on {device}: {exc}")

    detection_models = loaded
    detection_model = loaded[0]["model"] if loaded else None
    if not detection_models:
        print("[INFERENCE] WARNING: No detection model available. /detect will return 503.")


def next_model_entry() -> dict | None:
    global model_pool_index
    if not detection_models:
        load_model()
    if not detection_models:
        return None
    with model_pool_lock:
        entry = detection_models[model_pool_index % len(detection_models)]
        model_pool_index += 1
    return entry

def run_inference(image: Image.Image, metadata: dict = None):
    """
    Run detection on a single image chip.
    Returns list of detections with normalized bbox [x_center, y_center, width, height].
    """
    start_time = time.time()
    detections = []
    
    # Convert PIL to numpy array
    img_array = np.array(image)
    
    entry = next_model_entry()
    if entry is None:
        raise HTTPException(status_code=503, detail="No detection model is loaded; refusing to fabricate detections.")

    model = entry["model"]
    device = entry["device"]
    
    if entry["kind"] == "sahi" and hasattr(model, 'perform_inference'):
        # SAHI mode
        try:
            with entry["lock"]:
                result = get_sliced_prediction(
                    img_array,
                    model,
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
    
    elif YOLO_AVAILABLE and model is not None:
        # Plain YOLOv8 mode
        try:
            with entry["lock"]:
                results = model(
                    img_array,
                    device=device,
                    conf=CONFIDENCE_THRESHOLD,
                    iou=NMS_IOU_THRESHOLD,
                    max_det=MAX_DETECTIONS_PER_CHIP,
                    verbose=False,
                )
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
                        if len(classes) <= index:
                            continue
                        cls_id = int(classes[index])
                        cls_name = model.names.get(cls_id, f"class_{cls_id}")
                        cls_conf = float(confidences[index]) if len(confidences) > index else 0.0
                        if cls_conf < PER_CLASS_CONFIDENCE.get(cls_name, 0.0):
                            continue
                        detections.append({
                            "class": cls_name,
                            "bbox": [cx, cy, w, h],
                            "obb": [
                                max(0.0, min(1.0, flat[i] / (img_w if i % 2 == 0 else img_h)))
                                for i in range(8)
                            ],
                            "confidence": cls_conf,
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
                    cls_name = model.names.get(cls_id, f"class_{cls_id}")
                    cls_conf = float(box.conf[0])
                    if cls_conf < PER_CLASS_CONFIDENCE.get(cls_name, 0.0):
                        continue

                    detections.append({
                        "class": cls_name,
                        "bbox": [cx, cy, w, h],
                        "confidence": cls_conf
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
        "device": device
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
    
    result = await run_in_threadpool(run_inference, pil_image, meta)
    result["input_metadata"] = meta
    
    return result


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": bool(detection_models),
        "model_path": MODEL_PATH,
        "model_task": MODEL_TASK,
        "model_exists": os.path.exists(MODEL_PATH),
        "device": DEVICE,
        "devices": DEVICES,
        "cpu_threads": CPU_THREADS,
        "model_replicas": len(detection_models),
        "sahi_available": SAHI_AVAILABLE,
        "yolo_available": YOLO_AVAILABLE
    }
