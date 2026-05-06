import asyncio
import io
import json
import os
import shutil
import threading
import time
from typing import Any, Optional

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image
from starlette.concurrency import run_in_threadpool

from detection_policy import active_detection_policy, detection_decision

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


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


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


def default_engine_path(model_path: str) -> str:
    stem, _ = os.path.splitext(model_path)
    return f"{stem}.engine"


MODEL_PATH = resolve_model_path()
MODEL_TASK = os.getenv("MODEL_TASK") or ("obb" if "obb" in os.path.basename(MODEL_PATH).lower() else "detect")
GPU_MODEL = os.getenv("GPU_MODEL", "unknown")
INFERENCE_GPU_PROFILE = os.getenv("INFERENCE_GPU_PROFILE", "unknown")
DETECTION_POLICY = active_detection_policy()
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", str(DETECTION_POLICY["global_confidence_floor"])))
NMS_IOU_THRESHOLD = float(os.getenv("NMS_IOU_THRESHOLD", "0.5"))
MAX_DETECTIONS_PER_CHIP = env_int("MAX_DETECTIONS_PER_CHIP", 300)

YOLO_RUNTIME = os.getenv("YOLO_RUNTIME", "auto").strip().lower() or "auto"
YOLO_ENGINE_PATH = os.getenv("YOLO_ENGINE_PATH", default_engine_path(MODEL_PATH))
YOLO_TRT_PRECISION = os.getenv("YOLO_TRT_PRECISION", "fp16").strip().lower() or "fp16"
YOLO_IMGSZ = env_int("YOLO_IMGSZ", 1024)
YOLO_BATCH_MAX_SIZE = max(1, env_int("YOLO_BATCH_MAX_SIZE", 8))
YOLO_BATCH_TIMEOUT_MS = max(0.0, env_float("YOLO_BATCH_TIMEOUT_MS", 10.0))
YOLO_WARMUP = env_bool("YOLO_WARMUP", True)
YOLO_ENGINE_METADATA_PATH = f"{YOLO_ENGINE_PATH}.json"
YOLO_TRT_AUTO_EXPORT = env_bool("YOLO_TRT_AUTO_EXPORT", True)
YOLO_TRT_EXPORT_BATCHES_RAW = os.getenv("YOLO_TRT_EXPORT_BATCHES", f"{YOLO_BATCH_MAX_SIZE},4,2,1")
YOLO_TRT_WORKSPACE = env_float("YOLO_TRT_WORKSPACE", 4.0)
YOLO_TRT_CALIBRATION_DATA = os.getenv("YOLO_TRT_CALIBRATION_DATA", "").strip()
YOLO_TRT_FORCE_REEXPORT = env_bool("YOLO_TRT_FORCE_REEXPORT", False)


def load_engine_metadata() -> dict[str, Any]:
    if not os.path.exists(YOLO_ENGINE_METADATA_PATH):
        return {}
    try:
        with open(YOLO_ENGINE_METADATA_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[INFERENCE] WARNING: unable to read YOLO engine metadata {YOLO_ENGINE_METADATA_PATH}: {exc}")
        return {}


def parse_export_batches(raw: str) -> list[int]:
    batches: list[int] = []
    for item in raw.split(","):
        try:
            batch = int(item.strip())
        except (TypeError, ValueError):
            continue
        if batch > 0 and batch not in batches:
            batches.append(batch)
    for fallback in (YOLO_BATCH_MAX_SIZE, 4, 2, 1):
        if fallback > 0 and fallback not in batches:
            batches.append(fallback)
    return batches


YOLO_TRT_EXPORT_BATCHES = parse_export_batches(YOLO_TRT_EXPORT_BATCHES_RAW)
YOLO_ENGINE_METADATA: dict[str, Any] = {}
YOLO_ENGINE_MAX_BATCH = YOLO_BATCH_MAX_SIZE
YOLO_EFFECTIVE_BATCH_MAX_SIZE = YOLO_BATCH_MAX_SIZE
YOLO_AUTO_EXPORT_STATUS: dict[str, Any] = {"enabled": YOLO_TRT_AUTO_EXPORT, "status": "not_run"}


def refresh_yolo_engine_state() -> None:
    global YOLO_ENGINE_METADATA, YOLO_ENGINE_MAX_BATCH, YOLO_EFFECTIVE_BATCH_MAX_SIZE
    YOLO_ENGINE_METADATA = load_engine_metadata()
    metadata_batch = int(YOLO_ENGINE_METADATA.get("batch") or YOLO_BATCH_MAX_SIZE)
    YOLO_ENGINE_MAX_BATCH = max(1, env_int("YOLO_ENGINE_MAX_BATCH", metadata_batch))
    YOLO_EFFECTIVE_BATCH_MAX_SIZE = (
        min(YOLO_BATCH_MAX_SIZE, YOLO_ENGINE_MAX_BATCH)
        if os.path.exists(YOLO_ENGINE_PATH)
        else YOLO_BATCH_MAX_SIZE
    )


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


def cuda_unsupported_arch_policy() -> str:
    policy = os.getenv("CUDA_UNSUPPORTED_ARCH_POLICY", "cpu").strip().lower()
    return policy if policy in {"cpu", "cuda"} else "cpu"


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
        supported_arches = set(torch.cuda.get_arch_list())
        devices = []
        unsupported = []
        for index in range(torch.cuda.device_count()):
            capability = torch.cuda.get_device_capability(index)
            device_arch = f"sm_{capability[0]}{capability[1]}"
            name = torch.cuda.get_device_name(index)
            if not supported_arches or device_arch in supported_arches:
                devices.append(f"cuda:{index}")
            else:
                unsupported.append(f"cuda:{index} {name} {device_arch}")
        if devices:
            names = [torch.cuda.get_device_name(int(device.split(":")[1])) for device in devices]
            print(f"[INFERENCE] Using CUDA devices {', '.join(devices)}: {', '.join(names)} (torch CUDA {cuda_version})")
            return devices
        if unsupported and cuda_unsupported_arch_policy() == "cuda":
            devices = [f"cuda:{index}" for index in range(torch.cuda.device_count())]
            print(
                f"[INFERENCE] No visible CUDA device has an arch in the torch build arch list "
                f"({sorted(supported_arches)}); forcing CUDA devices by CUDA_UNSUPPORTED_ARCH_POLICY=cuda"
            )
            return devices
        print(
            f"[INFERENCE] No visible CUDA device has an arch in the torch build arch list "
            f"({sorted(supported_arches)}); unsupported devices: {unsupported}; using CPU"
        )
        return ["cpu"]

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


def configure_torch_runtime() -> None:
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.backends.cudnn.benchmark = True
    try:
        torch.set_float32_matmul_precision("high")
    except AttributeError:
        pass


configure_torch_runtime()
refresh_yolo_engine_state()

detection_model = None
detection_models: list[dict[str, Any]] = []
model_pool_lock = threading.Lock()
model_pool_index = 0


def current_gpu_signature(device: str) -> dict[str, Any]:
    if not device.startswith("cuda"):
        return {}
    try:
        import torch
        index = int(device.split(":", 1)[1]) if ":" in device else 0
        if not torch.cuda.is_available() or index >= torch.cuda.device_count():
            return {}
        return {
            "name": torch.cuda.get_device_name(index),
            "capability": list(torch.cuda.get_device_capability(index)),
            "torch_cuda": getattr(torch.version, "cuda", None),
        }
    except Exception as exc:
        return {"error": str(exc)}


def engine_metadata_matches_current_gpu(device: str) -> bool:
    if YOLO_TRT_FORCE_REEXPORT:
        return False
    if not os.path.exists(YOLO_ENGINE_PATH) or not YOLO_ENGINE_METADATA:
        return False
    gpu = current_gpu_signature(device)
    metadata_gpu = YOLO_ENGINE_METADATA.get("gpu") or {}
    if gpu and metadata_gpu:
        if metadata_gpu.get("name") != gpu.get("name"):
            return False
        if metadata_gpu.get("capability") != gpu.get("capability"):
            return False
    elif gpu:
        return False
    return (
        str(YOLO_ENGINE_METADATA.get("model")) == str(MODEL_PATH)
        and str(YOLO_ENGINE_METADATA.get("engine")) == str(YOLO_ENGINE_PATH)
        and str(YOLO_ENGINE_METADATA.get("precision")) == YOLO_TRT_PRECISION
        and int(YOLO_ENGINE_METADATA.get("imgsz") or 0) == YOLO_IMGSZ
        and bool(YOLO_ENGINE_METADATA.get("dynamic", True))
        and int(YOLO_ENGINE_METADATA.get("batch") or 0) > 0
    )


def write_engine_metadata(batch: int, device: str) -> None:
    metadata = {
        "model": MODEL_PATH,
        "engine": YOLO_ENGINE_PATH,
        "precision": YOLO_TRT_PRECISION,
        "imgsz": YOLO_IMGSZ,
        "batch": batch,
        "batch_requested": YOLO_BATCH_MAX_SIZE,
        "dynamic": True,
        "workspace": YOLO_TRT_WORKSPACE,
        "data": YOLO_TRT_CALIBRATION_DATA or None,
        "gpu": current_gpu_signature(device),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(YOLO_ENGINE_METADATA_PATH, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)
        handle.write("\n")


def maybe_auto_export_yolo_engine(device: str) -> None:
    global YOLO_AUTO_EXPORT_STATUS
    YOLO_AUTO_EXPORT_STATUS = {
        "enabled": YOLO_TRT_AUTO_EXPORT,
        "status": "not_needed",
        "attempts": [],
        "export_batches": YOLO_TRT_EXPORT_BATCHES,
    }
    if (
        not YOLO_TRT_AUTO_EXPORT
        or YOLO_RUNTIME == "pytorch"
        or MODEL_TASK != "obb"
        or not device.startswith("cuda")
        or not YOLO_AVAILABLE
    ):
        YOLO_AUTO_EXPORT_STATUS["status"] = "skipped"
        return
    if YOLO_TRT_PRECISION == "int8" and not YOLO_TRT_CALIBRATION_DATA:
        YOLO_AUTO_EXPORT_STATUS["status"] = "skipped_missing_int8_calibration"
        return
    if engine_metadata_matches_current_gpu(device):
        YOLO_AUTO_EXPORT_STATUS["status"] = "existing_compatible"
        YOLO_AUTO_EXPORT_STATUS["selected_batch"] = YOLO_ENGINE_METADATA.get("batch")
        return
    if not os.path.exists(MODEL_PATH):
        YOLO_AUTO_EXPORT_STATUS["status"] = "skipped_missing_model"
        return

    for batch in YOLO_TRT_EXPORT_BATCHES:
        attempt = {"batch": batch, "status": "started"}
        YOLO_AUTO_EXPORT_STATUS["attempts"].append(attempt)
        try:
            export_kwargs: dict[str, Any] = {
                "format": "engine",
                "imgsz": YOLO_IMGSZ,
                "batch": batch,
                "dynamic": True,
                "workspace": YOLO_TRT_WORKSPACE,
                "half": YOLO_TRT_PRECISION == "fp16",
                "int8": YOLO_TRT_PRECISION == "int8",
                "device": 0,
                "verbose": False,
            }
            if YOLO_TRT_CALIBRATION_DATA:
                export_kwargs["data"] = YOLO_TRT_CALIBRATION_DATA
            print(
                f"[INFERENCE] Auto-exporting YOLO TensorRT engine "
                f"batch={batch}, imgsz={YOLO_IMGSZ}, precision={YOLO_TRT_PRECISION}"
            )
            started = time.time()
            exported = YOLO(MODEL_PATH).export(**export_kwargs)
            exported_path = os.fspath(exported)
            if os.path.abspath(exported_path) != os.path.abspath(YOLO_ENGINE_PATH):
                shutil.move(exported_path, YOLO_ENGINE_PATH)
            write_engine_metadata(batch, device)
            refresh_yolo_engine_state()
            attempt["status"] = "success"
            attempt["elapsed_ms"] = round((time.time() - started) * 1000, 2)
            YOLO_AUTO_EXPORT_STATUS["status"] = "exported"
            YOLO_AUTO_EXPORT_STATUS["selected_batch"] = batch
            return
        except Exception as exc:
            attempt["status"] = "failed"
            attempt["error"] = str(exc)
            print(f"[INFERENCE] Auto TensorRT export failed for batch={batch}: {exc}")

    refresh_yolo_engine_state()
    YOLO_AUTO_EXPORT_STATUS["status"] = "failed_all_batches"


def yolo_runtime_candidates() -> list[tuple[str, str]]:
    runtime = YOLO_RUNTIME
    candidates: list[tuple[str, str]] = []
    if runtime not in {"auto", "tensorrt", "pytorch"}:
        print(f"[INFERENCE] WARNING: unsupported YOLO_RUNTIME={runtime}; using auto.")
        runtime = "auto"
    if runtime in {"auto", "tensorrt"}:
        candidates.append(("tensorrt", YOLO_ENGINE_PATH))
    if runtime in {"auto", "pytorch", "tensorrt"}:
        candidates.append(("pytorch", MODEL_PATH))
    return candidates


def yolo_predict_kwargs(device: str, batch_size: int) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "device": device,
        "conf": CONFIDENCE_THRESHOLD,
        "iou": NMS_IOU_THRESHOLD,
        "max_det": MAX_DETECTIONS_PER_CHIP,
        "imgsz": YOLO_IMGSZ,
        "batch": max(1, min(batch_size, YOLO_EFFECTIVE_BATCH_MAX_SIZE)),
        "verbose": False,
    }
    if YOLO_TRT_PRECISION == "fp16":
        kwargs["half"] = True
    return kwargs


def load_yolo_model(device: str) -> dict[str, Any]:
    if not YOLO_AVAILABLE:
        raise RuntimeError("ultralytics is unavailable")

    errors = []
    for runtime, path in yolo_runtime_candidates():
        if not path or not os.path.exists(path):
            errors.append(f"{runtime} path missing: {path}")
            continue
        try:
            model = YOLO(path)
            if runtime == "pytorch":
                try:
                    model.to(device)
                except Exception as exc:
                    print(f"[INFERENCE] WARNING: unable to move YOLO model to {device}: {exc}")
            entry = model_entry(model, device, "yolo", runtime=runtime, model_path=path)
            run_yolo_warmup(entry)
            return entry
        except Exception as exc:
            errors.append(f"{runtime} load failed from {path}: {exc}")
            print(f"[INFERENCE] YOLO {runtime} load failed on {device}: {exc}")

    raise RuntimeError("; ".join(errors) or "no YOLO runtime candidates loaded")


def run_yolo_warmup(entry: dict[str, Any]) -> None:
    entry["warmup_ms"] = None
    entry["warmup_error"] = None
    if not YOLO_WARMUP:
        return
    model = entry["model"]
    device = entry["device"]
    try:
        image = np.zeros((YOLO_IMGSZ, YOLO_IMGSZ, 3), dtype=np.uint8)
        start = time.time()
        with entry["lock"]:
            model([image], **yolo_predict_kwargs(device, 1))
        entry["warmup_ms"] = round((time.time() - start) * 1000, 2)
    except Exception as exc:
        entry["warmup_error"] = str(exc)
        print(f"[INFERENCE] YOLO warmup failed on {device}: {exc}")


def model_entry(model, device: str, kind: str, runtime: str | None = None, model_path: str | None = None) -> dict[str, Any]:
    return {
        "model": model,
        "device": device,
        "kind": kind,
        "runtime": runtime or kind,
        "model_path": model_path or MODEL_PATH,
        "lock": threading.Lock(),
        "warmup_ms": None,
        "warmup_error": None,
    }


def load_model():
    global detection_model, detection_models
    if detection_models:
        return
    if DEVICES:
        maybe_auto_export_yolo_engine(DEVICES[0])
        yolo_batcher.max_size = YOLO_EFFECTIVE_BATCH_MAX_SIZE
    if not os.path.exists(MODEL_PATH) and not os.path.exists(YOLO_ENGINE_PATH):
        print(f"[INFERENCE] WARNING: Model file does not exist: {MODEL_PATH}; engine file does not exist: {YOLO_ENGINE_PATH}")
        return

    loaded = []
    for device in DEVICES:
        if MODEL_TASK == "obb":
            try:
                loaded.append(load_yolo_model(device))
                entry = loaded[-1]
                print(
                    f"[INFERENCE] YOLO OBB {entry['runtime']} model loaded from "
                    f"{entry['model_path']} on {device}"
                )
            except Exception as exc:
                print(f"[INFERENCE] YOLO OBB load failed on {device}: {exc}")
        elif SAHI_AVAILABLE:
            try:
                loaded.append(model_entry(AutoDetectionModel.from_pretrained(
                    model_type="yolov8",
                    model_path=MODEL_PATH,
                    confidence_threshold=CONFIDENCE_THRESHOLD,
                    device=device,
                ), device, "sahi", runtime="sahi", model_path=MODEL_PATH))
                print(f"[INFERENCE] SAHI + YOLOv8 model loaded from {MODEL_PATH} on {device}")
            except Exception as exc:
                print(f"[INFERENCE] SAHI load failed on {device}: {exc}. Falling back to plain YOLOv8.")
                try:
                    loaded.append(load_yolo_model(device))
                except Exception as yolo_error:
                    print(f"[INFERENCE] Plain YOLOv8 fallback failed on {device}: {yolo_error}")
        else:
            try:
                loaded.append(load_yolo_model(device))
                entry = loaded[-1]
                print(
                    f"[INFERENCE] Plain YOLOv8 {entry['runtime']} model loaded from "
                    f"{entry['model_path']} on {device}"
                )
            except Exception as exc:
                print(f"[INFERENCE] Plain YOLOv8 load failed on {device}: {exc}")

    detection_models = loaded
    detection_model = loaded[0]["model"] if loaded else None
    if not detection_models:
        print("[INFERENCE] WARNING: No detection model available. /detect will return 503.")


def next_model_entry() -> dict[str, Any] | None:
    global model_pool_index
    if not detection_models:
        load_model()
    if not detection_models:
        return None
    with model_pool_lock:
        entry = detection_models[model_pool_index % len(detection_models)]
        model_pool_index += 1
    return entry


def _model_names(model) -> dict[int, str]:
    names = getattr(model, "names", {}) or {}
    if isinstance(names, dict):
        normalized = {}
        for key, value in names.items():
            try:
                normalized[int(key)] = str(value)
            except (TypeError, ValueError):
                continue
        return normalized
    return {index: name for index, name in enumerate(names)}


def detections_from_yolo_result(result, names: dict[int, str], image_size: tuple[int, int]) -> list[dict[str, Any]]:
    detections: list[dict[str, Any]] = []
    img_w, img_h = image_size
    obb = getattr(result, "obb", None)
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
            cls_name = names.get(cls_id, f"class_{cls_id}")
            cls_conf = float(confidences[index]) if len(confidences) > index else 0.0
            decision = detection_decision(cls_name, cls_conf, DETECTION_POLICY)
            if not decision["class_enabled"] or decision["review_status"] == "below_class_threshold":
                continue
            detections.append({
                "class": decision["parent_class"],
                "original_class": decision["original_class"],
                "parent_class": decision["parent_class"],
                "bbox": [cx, cy, w, h],
                "obb": [
                    max(0.0, min(1.0, flat[i] / (img_w if i % 2 == 0 else img_h)))
                    for i in range(8)
                ],
                "confidence": cls_conf,
                **decision,
            })
        return detections

    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return detections
    for box in boxes:
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        cx = (x1 + x2) / 2 / img_w
        cy = (y1 + y2) / 2 / img_h
        w = (x2 - x1) / img_w
        h = (y2 - y1) / img_h
        cls_id = int(box.cls[0])
        cls_name = names.get(cls_id, f"class_{cls_id}")
        cls_conf = float(box.conf[0])
        decision = detection_decision(cls_name, cls_conf, DETECTION_POLICY)
        if not decision["class_enabled"] or decision["review_status"] == "below_class_threshold":
            continue

        detections.append({
            "class": decision["parent_class"],
            "original_class": decision["original_class"],
            "parent_class": decision["parent_class"],
            "bbox": [cx, cy, w, h],
            "confidence": cls_conf,
            **decision,
        })
    return detections


def yolo_response(
    start_time: float,
    detections: list[dict[str, Any]],
    entry: dict[str, Any],
    batch_size: int = 1,
) -> dict[str, Any]:
    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        "model": entry.get("model_path") or MODEL_PATH,
        "task": MODEL_TASK,
        "device": entry["device"],
        "gpu_model": GPU_MODEL,
        "gpu_profile": INFERENCE_GPU_PROFILE,
        "runtime": entry.get("runtime"),
        "batch_size": batch_size,
        "model_version": DETECTION_POLICY["model_version"],
        "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
        "threshold_profile": DETECTION_POLICY["threshold_profile"],
        "global_confidence_floor": CONFIDENCE_THRESHOLD,
    }


def run_sahi_inference(image: Image.Image, entry: dict[str, Any]) -> dict[str, Any]:
    start_time = time.time()
    detections = []
    img_array = np.array(image)
    model = entry["model"]

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
                verbose=0,
            )

        for obj in result.object_prediction_list:
            bbox = obj.bbox
            x1, y1, x2, y2 = bbox.minx, bbox.miny, bbox.maxx, bbox.maxy
            img_w, img_h = image.size
            cx = (x1 + x2) / 2 / img_w
            cy = (y1 + y2) / 2 / img_h
            w = (x2 - x1) / img_w
            h = (y2 - y1) / img_h
            cls_name = obj.category.name if hasattr(obj.category, "name") else str(obj.category.id)
            cls_conf = float(obj.score.value)
            decision = detection_decision(cls_name, cls_conf, DETECTION_POLICY)
            if not decision["class_enabled"] or decision["review_status"] == "below_class_threshold":
                continue
            detections.append({
                "class": decision["parent_class"],
                "original_class": decision["original_class"],
                "parent_class": decision["parent_class"],
                "bbox": [cx, cy, w, h],
                "confidence": cls_conf,
                **decision,
            })
    except Exception as exc:
        print(f"[INFERENCE] SAHI inference error: {exc}")

    return yolo_response(start_time, detections, entry)


def _coerce_yolo_results(raw_results, expected_count: int) -> list[Any]:
    if expected_count == 1 and not isinstance(raw_results, list):
        return [raw_results]
    if isinstance(raw_results, tuple):
        raw_results = list(raw_results)
    if not isinstance(raw_results, list):
        try:
            raw_results = list(raw_results)
        except TypeError as exc:
            raise TypeError(f"Expected batched YOLO results, got {type(raw_results).__name__}") from exc
    if len(raw_results) != expected_count:
        raise RuntimeError(f"YOLO returned {len(raw_results)} results for {expected_count} images")
    return raw_results


def run_yolo_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entry = next_model_entry()
    if entry is None:
        raise HTTPException(status_code=503, detail="No detection model is loaded; refusing to fabricate detections.")
    if entry["kind"] != "yolo":
        return [run_sahi_inference(item["image"], entry) for item in items]

    model = entry["model"]
    device = entry["device"]
    image_arrays = [item["image_array"] for item in items]
    try:
        with entry["lock"]:
            raw_results = model(
                image_arrays,
                **yolo_predict_kwargs(device, len(image_arrays)),
            )
        results = _coerce_yolo_results(raw_results, len(items))
    except Exception as exc:
        print(f"[INFERENCE] YOLO inference error: {exc}")
        raise HTTPException(status_code=500, detail=f"YOLO inference failed: {exc}") from exc

    names = _model_names(model)
    return [
        yolo_response(
            item["start_time"],
            detections_from_yolo_result(result, names, item["image_size"]),
            entry,
            batch_size=len(items),
        )
        for item, result in zip(items, results)
    ]


def run_inference(image: Image.Image, image_array: np.ndarray, image_size: tuple[int, int], metadata: dict | None = None):
    item = {
        "image": image,
        "image_array": image_array,
        "image_size": image_size,
        "start_time": time.time(),
    }
    return run_yolo_batch([item])[0]


class YoloInferenceBatcher:
    def __init__(self, max_size: int, timeout_ms: float):
        self.max_size = max(1, max_size)
        self.timeout_s = max(0.0, timeout_ms / 1000.0)
        self._queue: asyncio.Queue | None = None
        self._worker_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.total_batches = 0
        self.total_items = 0
        self.last_batch_size = 0
        self.last_batch_ms = None

    async def submit(
        self,
        image: Image.Image,
        image_array: np.ndarray | dict | None = None,
        image_size: tuple[int, int] | None = None,
        metadata: Optional[dict] = None,
    ) -> dict[str, Any]:
        if isinstance(image_array, dict) and image_size is None and metadata is None:
            metadata = image_array
            image_array = None
        if image_array is None:
            image_array = np.array(image)
        if image_size is None:
            image_size = image.size
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        item = {
            "image": image,
            "image_array": image_array,
            "image_size": image_size,
            "start_time": time.time(),
            "future": future,
        }
        queue = await self._ensure_queue()
        await queue.put(item)
        return await future

    async def _ensure_queue(self) -> asyncio.Queue:
        async with self._lock:
            if self._queue is None:
                self._queue = asyncio.Queue()
            if self._worker_task is None or self._worker_task.done():
                self._worker_task = asyncio.create_task(self._worker(), name="yolo-batcher")
            return self._queue

    async def _worker(self) -> None:
        assert self._queue is not None
        queue = self._queue
        while True:
            first = await queue.get()
            batch = [first]
            deadline = asyncio.get_running_loop().time() + self.timeout_s
            while len(batch) < self.max_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(queue.get(), timeout=remaining))
                except asyncio.TimeoutError:
                    break

            batch_start = time.time()
            payload = [
                {
                    "image": item["image"],
                    "image_array": item["image_array"],
                    "image_size": item["image_size"],
                    "start_time": item["start_time"],
                }
                for item in batch
            ]
            try:
                responses = await run_in_threadpool(run_yolo_batch, payload)
            except Exception as exc:
                for item in batch:
                    future = item["future"]
                    if not future.done():
                        future.set_exception(exc)
            else:
                for item, response in zip(batch, responses):
                    future = item["future"]
                    if not future.done():
                        future.set_result(response)
                self.total_batches += 1
                self.total_items += len(batch)
                self.last_batch_size = len(batch)
                self.last_batch_ms = round((time.time() - batch_start) * 1000, 2)
            finally:
                for _ in batch:
                    queue.task_done()

    def stats(self) -> dict[str, Any]:
        queued_items = self._queue.qsize() if self._queue is not None else 0
        avg_batch_size = round(self.total_items / self.total_batches, 2) if self.total_batches else 0
        return {
            "enabled": self.max_size > 1,
            "max_size": self.max_size,
            "timeout_ms": YOLO_BATCH_TIMEOUT_MS,
            "queued_items": queued_items,
            "total_batches": self.total_batches,
            "total_items": self.total_items,
            "avg_batch_size": avg_batch_size,
            "last_batch_size": self.last_batch_size,
            "last_batch_ms": self.last_batch_ms,
        }


yolo_batcher = YoloInferenceBatcher(YOLO_EFFECTIVE_BATCH_MAX_SIZE, YOLO_BATCH_TIMEOUT_MS)


@app.on_event("startup")
def startup_event():
    load_model()


def decode_image(contents: bytes) -> tuple[Image.Image, np.ndarray, tuple[int, int]]:
    pil_image = Image.open(io.BytesIO(contents))
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    image_array = np.array(pil_image)
    return pil_image, image_array, pil_image.size


@app.post("/detect")
async def detect_objects(
    image: UploadFile = File(...),
    metadata: str = Form("{}"),
):
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        meta = {}

    contents = await image.read()
    try:
        pil_image, image_array, image_size = await run_in_threadpool(decode_image, contents)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image file: {exc}")

    if YOLO_BATCH_MAX_SIZE > 1 and MODEL_TASK == "obb":
        result = await yolo_batcher.submit(pil_image, image_array, image_size, meta)
    else:
        result = await run_in_threadpool(run_inference, pil_image, image_array, image_size, meta)
    result["input_metadata"] = meta
    return result


def torch_info() -> dict[str, Any]:
    info: dict[str, Any] = {"torch": None, "torch_cuda": None, "cuda_runtime": None}
    try:
        import torch
        info["torch"] = torch.__version__
        info["torch_cuda"] = getattr(torch.version, "cuda", None)
        if torch.cuda.is_available():
            info["cuda_runtime"] = {
                "device_count": torch.cuda.device_count(),
                "names": [torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())],
                "capabilities": [
                    list(torch.cuda.get_device_capability(index))
                    for index in range(torch.cuda.device_count())
                ],
                "arch_list": torch.cuda.get_arch_list(),
            }
    except Exception as exc:
        info["error"] = str(exc)
    return info


@app.get("/health")
def health():
    replicas = [
        {
            "device": entry["device"],
            "kind": entry["kind"],
            "runtime": entry.get("runtime"),
            "model_path": entry.get("model_path"),
            "warmup_ms": entry.get("warmup_ms"),
            "warmup_error": entry.get("warmup_error"),
        }
        for entry in detection_models
    ]
    active_runtime = replicas[0]["runtime"] if replicas else None
    active_model_path = replicas[0]["model_path"] if replicas else MODEL_PATH
    return {
        "status": "ok",
        "model_loaded": bool(detection_models),
        "model_path": active_model_path,
        "pytorch_model_path": MODEL_PATH,
        "engine_path": YOLO_ENGINE_PATH,
        "engine_exists": os.path.exists(YOLO_ENGINE_PATH),
        "engine_metadata_path": YOLO_ENGINE_METADATA_PATH,
        "engine_metadata_exists": os.path.exists(YOLO_ENGINE_METADATA_PATH),
        "engine_metadata": YOLO_ENGINE_METADATA,
        "model_task": MODEL_TASK,
        "model_exists": os.path.exists(active_model_path) if active_model_path else False,
        "device": DEVICE,
        "devices": DEVICES,
        "gpu_model": GPU_MODEL,
        "gpu_profile": INFERENCE_GPU_PROFILE,
        "cpu_threads": CPU_THREADS,
        "model_replicas": len(detection_models),
        "replicas": replicas,
        "runtime": active_runtime,
        "runtime_requested": YOLO_RUNTIME,
        "trt_precision": YOLO_TRT_PRECISION,
        "yolo_imgsz": YOLO_IMGSZ,
        "batch_max_requested": YOLO_BATCH_MAX_SIZE,
        "batch_max_effective": YOLO_EFFECTIVE_BATCH_MAX_SIZE,
        "auto_export": YOLO_AUTO_EXPORT_STATUS,
        "auto_export_batches": YOLO_TRT_EXPORT_BATCHES,
        "auto_export_workspace": YOLO_TRT_WORKSPACE,
        "warmup_enabled": YOLO_WARMUP,
        "batching": yolo_batcher.stats(),
        "sahi_available": SAHI_AVAILABLE,
        "yolo_available": YOLO_AVAILABLE,
        "detection_policy": DETECTION_POLICY,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "nms_iou_threshold": NMS_IOU_THRESHOLD,
        "max_detections_per_chip": MAX_DETECTIONS_PER_CHIP,
        "torch_info": torch_info(),
    }
