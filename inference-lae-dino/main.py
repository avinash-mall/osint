import os
import io
import time
import json
import threading
from typing import Optional, List

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from starlette.concurrency import run_in_threadpool
import numpy as np
from PIL import Image

from detection_policy import active_detection_policy, detection_decision

try:
    from mmengine.config import Config
    from mmengine.dataset import Compose
    from mmdet.apis import init_detector, inference_detector
    from mmdet.utils import get_test_pipeline_cfg
    MMDET_AVAILABLE = True
except ImportError as exc:
    print(f"[INFERENCE-LAE] WARNING: mmdet/mmengine import failed: {exc}")
    MMDET_AVAILABLE = False


app = FastAPI(title="Magritte AIP Node - LAE-DINO Open-Vocabulary Inference")


LAE_DINO_CONFIG = os.getenv(
    "LAE_DINO_CONFIG",
    "/opt/LAE-DINO/mmdetection_lae/configs/lae_dino/lae_dino_swin-t_pretrain_LAE-1M.py",
)
LAE_DINO_CHECKPOINT = os.getenv(
    "LAE_DINO_CHECKPOINT",
    "/opt/LAE-DINO/weights/checkpoints/lae_dino_swint_lae1m-28ca3a15.pth",
)
LAE_DINO_BERT_PATH = os.getenv(
    "LAE_DINO_BERT_PATH",
    "/opt/LAE-DINO/weights/bert-base-uncased",
)

DEFAULT_TEXT_PROMPT = os.getenv(
    "DEFAULT_TEXT_PROMPT",
    "aircraft . ship . vehicle . military_vehicle . storage_tank . "
    "bridge . harbor . airfield . building . infrastructure",
)

DETECTION_POLICY = active_detection_policy()
CONFIDENCE_THRESHOLD = float(
    os.getenv("CONFIDENCE_THRESHOLD", str(DETECTION_POLICY["global_confidence_floor"]))
)
MAX_DETECTIONS_PER_CHIP = int(os.getenv("MAX_DETECTIONS_PER_CHIP", "300"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "lae-dino-swint-lae1m")


def available_cpu_count() -> int:
    if hasattr(os, "sched_getaffinity"):
        try:
            return len(os.sched_getaffinity(0))
        except OSError:
            pass
    return os.cpu_count() or 1


def normalize_device_list(value: str) -> List[str]:
    devices = []
    for item in value.split(","):
        device = item.strip()
        if not device:
            continue
        if device.isdigit():
            device = f"cuda:{device}"
        devices.append(device)
    return devices or ["cpu"]


def resolve_devices() -> List[str]:
    requested = os.getenv("DEVICE", "auto").strip()
    if requested and requested.lower() != "auto":
        devices = normalize_device_list(requested)
        print(f"[INFERENCE-LAE] Using requested devices: {', '.join(devices)}")
        return devices
    try:
        import torch
    except ImportError:
        print("[INFERENCE-LAE] WARNING: torch is unavailable; falling back to CPU.")
        return ["cpu"]

    cuda_version = getattr(torch.version, "cuda", None)
    if torch.cuda.is_available():
        devices = [f"cuda:{i}" for i in range(torch.cuda.device_count())]
        names = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        print(
            f"[INFERENCE-LAE] Using CUDA devices {', '.join(devices)}: "
            f"{', '.join(names)} (torch CUDA {cuda_version})"
        )
        return devices

    print(
        "[INFERENCE-LAE] WARNING: CUDA unavailable; using CPU. "
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
    print(f"[INFERENCE-LAE] Using {threads} CPU compute threads per process.")
    return threads


CPU_THREADS = configure_cpu_threads()


detection_models: List[dict] = []
model_pool_lock = threading.Lock()
model_pool_index = 0


def _patch_bert_path(cfg: "Config") -> None:
    """Point the model's text encoder at the on-disk bert weights so it
    loads fully offline. The upstream config sets `lang_model_name` to
    'bert-base-uncased' (HF hub identifier); we redirect it to the local
    directory baked into the image."""
    if not os.path.isdir(LAE_DINO_BERT_PATH):
        print(f"[INFERENCE-LAE] WARNING: BERT path missing: {LAE_DINO_BERT_PATH}")
        return
    candidates = [
        ("model", "language_model", "name"),
        ("model", "language_model", "lang_model_name"),
        ("model", "text_encoder", "name"),
        ("model", "text_encoder", "lang_model_name"),
        ("model", "bert_model_name",),
        ("model", "lang_model_name",),
    ]
    cfg_dict = cfg._cfg_dict if hasattr(cfg, "_cfg_dict") else cfg
    patched = False
    for path in candidates:
        node = cfg_dict
        ok = True
        for key in path[:-1]:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok and isinstance(node, dict) and path[-1] in node:
            node[path[-1]] = LAE_DINO_BERT_PATH
            print(f"[INFERENCE-LAE] Patched config {'.'.join(path)} -> {LAE_DINO_BERT_PATH}")
            patched = True
    if not patched:
        print(
            "[INFERENCE-LAE] WARNING: Did not find a known BERT config key to patch. "
            "Relying on TRANSFORMERS_OFFLINE + HF cache for offline load."
        )


def _strip_dataset_configs(cfg: "Config") -> None:
    """Remove top-level dataloader/evaluator keys that reference annotation
    files not present at inference time. Class names are supplied via text
    prompts so we don't need dataset metainfo at model init."""
    _DATASET_KEYS = (
        "train_dataloader", "val_dataloader", "test_dataloader",
        "train_evaluator", "val_evaluator", "test_evaluator",
    )
    cfg_dict = cfg._cfg_dict if hasattr(cfg, "_cfg_dict") else cfg
    for key in _DATASET_KEYS:
        if key in cfg_dict:
            del cfg_dict[key]


def _build_model(device: str):
    cfg = Config.fromfile(LAE_DINO_CONFIG)
    _patch_bert_path(cfg)
    # Snapshot the raw pipeline config dict BEFORE stripping test_dataloader.
    # We cannot build Compose yet because FixScaleResize and other custom LAE-DINO
    # transforms are registered by init_detector's init_default_scope call.
    raw_pipeline = get_test_pipeline_cfg(cfg).copy()
    _strip_dataset_configs(cfg)
    # palette='random' skips the test_dataloader.dataset block inside init_detector.
    model = init_detector(cfg, LAE_DINO_CHECKPOINT, palette="random", device=device)
    # Now that custom transforms are registered, build Compose and attach to model.
    raw_pipeline[0].type = "mmdet.LoadImageFromNDArray"
    model._lae_test_pipeline = Compose(raw_pipeline)
    return model


def load_model() -> None:
    global detection_models
    if detection_models:
        return
    if not MMDET_AVAILABLE:
        print("[INFERENCE-LAE] mmdet unavailable; skipping model load.")
        return
    if not os.path.exists(LAE_DINO_CHECKPOINT):
        print(f"[INFERENCE-LAE] WARNING: checkpoint missing: {LAE_DINO_CHECKPOINT}")
        return
    if not os.path.exists(LAE_DINO_CONFIG):
        print(f"[INFERENCE-LAE] WARNING: config missing: {LAE_DINO_CONFIG}")
        return

    loaded = []
    for device in DEVICES:
        try:
            model = _build_model(device)
            loaded.append({
                "model": model,
                "device": device,
                "lock": threading.Lock(),
            })
            print(f"[INFERENCE-LAE] LAE-DINO loaded from {LAE_DINO_CHECKPOINT} on {device}")
        except Exception as exc:
            print(f"[INFERENCE-LAE] Load failed on {device}: {exc}")

    detection_models = loaded
    if not detection_models:
        print("[INFERENCE-LAE] WARNING: No model replicas loaded. /detect will return 503.")


def next_model_entry() -> Optional[dict]:
    global model_pool_index
    if not detection_models:
        load_model()
    if not detection_models:
        return None
    with model_pool_lock:
        entry = detection_models[model_pool_index % len(detection_models)]
        model_pool_index += 1
    return entry


def resolve_prompt(metadata: Optional[dict]) -> str:
    if isinstance(metadata, dict):
        prompt = metadata.get("text_prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return DEFAULT_TEXT_PROMPT


def _split_prompt_tokens(prompt: str) -> List[str]:
    return [tok.strip() for tok in prompt.split(".") if tok.strip()]


def _run_detector(model, image_array: np.ndarray, prompt: str):
    """Call inference_detector, passing the pre-built test pipeline so mmdet
    doesn't re-read test_dataloader from model.cfg (which we stripped)."""
    test_pipeline = getattr(model, "_lae_test_pipeline", None)
    base_kwargs = {"test_pipeline": test_pipeline} if test_pipeline else {}
    last_exc: Optional[Exception] = None
    for extra in (
        {"text_prompt": prompt, "custom_entities": True},
        {"texts": prompt, "custom_entities": True},
        {"text_prompt": prompt},
        {"texts": prompt},
    ):
        try:
            return inference_detector(model, image_array, **base_kwargs, **extra)
        except TypeError as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    return inference_detector(model, image_array, **base_kwargs)


def run_inference(image: Image.Image, metadata: Optional[dict] = None) -> dict:
    start_time = time.time()
    detections: List[dict] = []

    entry = next_model_entry()
    if entry is None:
        raise HTTPException(
            status_code=503,
            detail="No LAE-DINO model is loaded; refusing to fabricate detections.",
        )

    model = entry["model"]
    device = entry["device"]
    prompt = resolve_prompt(metadata)
    prompt_tokens = _split_prompt_tokens(prompt)

    img_array = np.array(image)
    img_w, img_h = image.size

    try:
        with entry["lock"]:
            result = _run_detector(model, img_array, prompt)
    except Exception as exc:
        print(f"[INFERENCE-LAE] Inference error: {exc}")
        raise HTTPException(status_code=500, detail=f"LAE-DINO inference failed: {exc}")

    pred = getattr(result, "pred_instances", None)
    if pred is None:
        return _empty_response(start_time, device, prompt)

    try:
        bboxes = pred.bboxes.detach().cpu().numpy()
        scores = pred.scores.detach().cpu().numpy()
        label_idx = pred.labels.detach().cpu().numpy()
    except AttributeError as exc:
        print(f"[INFERENCE-LAE] Unexpected pred_instances shape: {exc}")
        return _empty_response(start_time, device, prompt)

    label_names = getattr(pred, "label_names", None)
    if label_names is not None and not isinstance(label_names, list):
        try:
            label_names = list(label_names)
        except TypeError:
            label_names = None

    order = np.argsort(-scores)
    if MAX_DETECTIONS_PER_CHIP > 0:
        order = order[:MAX_DETECTIONS_PER_CHIP]

    for i in order:
        score = float(scores[i])
        if score < CONFIDENCE_THRESHOLD:
            continue

        if label_names is not None and i < len(label_names):
            cls_name = str(label_names[i])
        else:
            idx = int(label_idx[i])
            cls_name = prompt_tokens[idx] if 0 <= idx < len(prompt_tokens) else f"class_{idx}"

        decision = detection_decision(cls_name, score, DETECTION_POLICY)
        if not decision["class_enabled"] or decision["review_status"] == "below_class_threshold":
            continue

        x1, y1, x2, y2 = (float(v) for v in bboxes[i][:4])
        cx = (x1 + x2) / 2.0 / img_w
        cy = (y1 + y2) / 2.0 / img_h
        bw = (x2 - x1) / img_w
        bh = (y2 - y1) / img_h

        detections.append({
            "class": decision["parent_class"],
            "original_class": decision["original_class"],
            "parent_class": decision["parent_class"],
            "bbox": [cx, cy, bw, bh],
            "confidence": score,
            **decision,
        })

    processing_time = time.time() - start_time
    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": round(processing_time * 1000, 2),
        "model": LAE_DINO_CHECKPOINT,
        "task": "open_vocab_detect",
        "device": device,
        "model_version": MODEL_VERSION,
        "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
        "threshold_profile": DETECTION_POLICY["threshold_profile"],
        "global_confidence_floor": CONFIDENCE_THRESHOLD,
        "text_prompt": prompt,
    }


def _empty_response(start_time: float, device: str, prompt: str) -> dict:
    return {
        "status": "success",
        "detections": [],
        "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        "model": LAE_DINO_CHECKPOINT,
        "task": "open_vocab_detect",
        "device": device,
        "model_version": MODEL_VERSION,
        "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
        "threshold_profile": DETECTION_POLICY["threshold_profile"],
        "global_confidence_floor": CONFIDENCE_THRESHOLD,
        "text_prompt": prompt,
    }


@app.on_event("startup")
def startup_event():
    load_model()


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
        pil_image = Image.open(io.BytesIO(contents))
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image file: {exc}")

    result = await run_in_threadpool(run_inference, pil_image, meta)
    result["input_metadata"] = meta
    return result


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_loaded": bool(detection_models),
        "model_path": LAE_DINO_CHECKPOINT,
        "model_task": "open_vocab_detect",
        "model_exists": os.path.exists(LAE_DINO_CHECKPOINT),
        "config_path": LAE_DINO_CONFIG,
        "config_exists": os.path.exists(LAE_DINO_CONFIG),
        "bert_path": LAE_DINO_BERT_PATH,
        "bert_exists": os.path.isdir(LAE_DINO_BERT_PATH),
        "device": DEVICE,
        "devices": DEVICES,
        "cpu_threads": CPU_THREADS,
        "model_replicas": len(detection_models),
        "mmdet_available": MMDET_AVAILABLE,
        "default_text_prompt": DEFAULT_TEXT_PROMPT,
        "model_version": MODEL_VERSION,
        "detection_policy": DETECTION_POLICY,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "max_detections_per_chip": MAX_DETECTIONS_PER_CHIP,
    }
