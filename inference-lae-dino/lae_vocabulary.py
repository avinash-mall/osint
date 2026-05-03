from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable


LAE_80C_CLASSES: tuple[str, ...] = (
    "airplane",
    "airport",
    "groundtrackfield",
    "harbor",
    "baseballfield",
    "overpass",
    "basketballcourt",
    "bridge",
    "stadium",
    "storagetank",
    "tenniscourt",
    "expressway service area",
    "trainstation",
    "expressway toll station",
    "vehicle",
    "golffield",
    "windmill",
    "dam",
    "helicopter",
    "roundabout",
    "soccer ball field",
    "swimming pool",
    "container crane",
    "helipad",
    "Bus",
    "Cargo Truck",
    "Dry Cargo Ship",
    "Dump Truck",
    "Engineering Ship",
    "Excavator",
    "Fishing Boat",
    "Intersection",
    "Liquid Cargo Ship",
    "Motorboat",
    "Passenger Ship",
    "Small Car",
    "Tractor",
    "Trailer",
    "Truck Tractor",
    "Tugboat",
    "Van",
    "Warship",
    "working condensing tower",
    "unworking condensing tower",
    "working chimney",
    "unworking chimney",
    "Fixed-wing Aircraft",
    "Small Aircraft",
    "Cargo Plane",
    "Pickup Truck",
    "Utility Truck",
    "Passenger Car",
    "Cargo Car",
    "Flat Car",
    "Locomotive",
    "Sailboat",
    "Barge",
    "Ferry",
    "Yacht",
    "Oil Tanker",
    "Engineering Vehicle",
    "Tower crane",
    "Reach Stacker",
    "Straddle Carrier",
    "Mobile Crane",
    "Haul Truck",
    "Front loader/Bulldozer",
    "Cement Mixer",
    "Ground Grader",
    "Hut/Tent",
    "Shed",
    "Building",
    "Aircraft Hangar",
    "Damaged Building",
    "Facility",
    "Construction Site",
    "Shipping container lot",
    "Shipping Container",
    "Pylon",
    "Tower",
)


COCO_CLASSES: tuple[str, ...] = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)


def prompt_from_classes(classes: Iterable[str]) -> str:
    names = [str(item).strip() for item in classes if str(item).strip()]
    return " . ".join(names)


def split_prompt_tokens(prompt: str) -> list[str]:
    return [tok.strip() for tok in prompt.split(".") if tok.strip()]


def chunk_classes(classes: Iterable[str], chunk_size: int) -> list[tuple[str, ...]]:
    names = tuple(str(item).strip() for item in classes if str(item).strip())
    size = max(1, int(chunk_size or 1))
    return [names[index:index + size] for index in range(0, len(names), size)]


def load_vocabulary_file(path: str | os.PathLike[str]) -> tuple[str, ...]:
    vocab_path = Path(path)
    raw = vocab_path.read_text(encoding="utf-8").strip()
    if not raw:
        return ()

    if raw.startswith("["):
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return tuple(str(item).strip() for item in parsed if str(item).strip())

    if raw.startswith("{"):
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            values = parsed.get("classes") or parsed.get("categories") or []
            return tuple(str(item).strip() for item in values if str(item).strip())

    return tuple(line.strip() for line in raw.splitlines() if line.strip())
