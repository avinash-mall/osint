#!/usr/bin/env python3
"""
Prepare overhead-imagery datasets for SAHI + YOLOv8 training.

Default output is the repository-level training_dataset/ directory:

  training_dataset/
    raw/{xview,dota,rareplanes,fair1m}/
    yolo/{train,val,test}/{images,labels}/
    yolo/data.yaml
    yolo/classes.json
    yolo/manifest.jsonl

The script converts available raw datasets into YOLO OBB labels:

  class x1 y1 x2 y2 x3 y3 x4 y4

Coordinates are normalized by tile size. DOTA and FAIR1M keep their native
oriented corners. Datasets with horizontal boxes are represented as axis-aligned
OBB polygons so all classes can train in one YOLO OBB model.

Several source datasets require registration or very large cloud downloads. The
download command is therefore best-effort and does not bypass dataset terms. If a
download cannot be automated, place the raw files under training_dataset/raw/<name>
and run the prepare command.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps
from tqdm import tqdm

Image.MAX_IMAGE_PIXELS = None


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "training_dataset"
DEFAULT_YOLO_ROOT = DEFAULT_ROOT / "yolo"
DEFAULT_RAW_ROOT = DEFAULT_ROOT / "raw"
TILE_SIZE = 640
OVERLAP = 0.2
DEFAULT_SPLIT = (0.8, 0.1, 0.1)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".jp2"}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
XVIEW_ARCHIVE_STEMS = {"train_images", "train_labels", "val_images"}
XVIEW_ARCHIVES = {
    f"{stem}{suffix}"
    for stem in XVIEW_ARCHIVE_STEMS
    for suffix in (".zip", ".tgz", ".tar.gz", ".tar")
}


XVIEW_TYPE_ID_TO_NAME = {
    11: "xview_fixed_wing_aircraft",
    12: "xview_small_aircraft",
    13: "xview_cargo_plane",
    15: "xview_helicopter",
    17: "xview_passenger_vehicle",
    18: "xview_small_car",
    19: "xview_bus",
    20: "xview_pickup_truck",
    21: "xview_utility_truck",
    23: "xview_truck",
    24: "xview_cargo_truck",
    25: "xview_truck_with_box",
    26: "xview_truck_tractor",
    27: "xview_trailer",
    28: "xview_truck_with_flatbed",
    29: "xview_truck_with_liquid",
    32: "xview_crane_truck",
    33: "xview_railway_vehicle",
    34: "xview_passenger_car",
    35: "xview_cargo_car",
    36: "xview_flat_car",
    37: "xview_tank_car",
    38: "xview_locomotive",
    40: "xview_maritime_vessel",
    41: "xview_motorboat",
    42: "xview_sailboat",
    44: "xview_tugboat",
    45: "xview_barge",
    47: "xview_fishing_vessel",
    49: "xview_ferry",
    50: "xview_yacht",
    51: "xview_container_ship",
    52: "xview_oil_tanker",
    53: "xview_engineering_vehicle",
    54: "xview_tower_crane",
    55: "xview_container_crane",
    56: "xview_reach_stacker",
    57: "xview_straddle_carrier",
    59: "xview_mobile_crane",
    60: "xview_dump_truck",
    61: "xview_haul_truck",
    62: "xview_scraper_tractor",
    63: "xview_front_loader_bulldozer",
    64: "xview_excavator",
    65: "xview_cement_mixer",
    66: "xview_ground_grader",
    71: "xview_hut_tent",
    72: "xview_shed",
    73: "xview_building",
    74: "xview_aircraft_hangar",
    76: "xview_damaged_demolished_building",
    77: "xview_facility",
    79: "xview_construction_site",
    83: "xview_vehicle_lot",
    84: "xview_helipad",
    86: "xview_storage_tank",
    89: "xview_shipping_container_lot",
    91: "xview_shipping_container",
    93: "xview_pylon",
    94: "xview_tower",
}

DOTA_CLASSES = [
    "dota_plane",
    "dota_baseball_diamond",
    "dota_bridge",
    "dota_ground_track_field",
    "dota_small_vehicle",
    "dota_large_vehicle",
    "dota_ship",
    "dota_tennis_court",
    "dota_basketball_court",
    "dota_storage_tank",
    "dota_soccer_ball_field",
    "dota_roundabout",
    "dota_harbor",
    "dota_swimming_pool",
    "dota_helicopter",
    "dota_container_crane",
    "dota_airport",
    "dota_helipad",
]

@dataclass(frozen=True)
class Annotation:
    label: str
    polygon: tuple[float, float, float, float, float, float, float, float]
    source: str

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return polygon_bbox(self.polygon)


@dataclass
class ConversionStats:
    dataset: str
    images_seen: int = 0
    images_written: int = 0
    tiles_written: int = 0
    labels_written: int = 0
    skipped: int = 0


class ClassRegistry:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.index: dict[str, int] = {}

    def add(self, name: str) -> int:
        clean = sanitize_label(name)
        if clean not in self.index:
            self.index[clean] = len(self.names)
            self.names.append(clean)
        return self.index[clean]

    def preload(self, names: Iterable[str]) -> None:
        for name in names:
            self.add(name)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"names": self.names}, indent=2) + "\n", encoding="utf-8")


def sanitize_label(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    replacements = {
        "&": "and",
        "/": "_",
        "\\": "_",
        "-": "_",
        " ": "_",
        "(": "",
        ")": "",
        "[": "",
        "]": "",
        ",": "",
        ".": "",
        "'": "",
        '"': "",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = "_".join(part for part in text.split("_") if part)
    return text or "unknown"


def repo_relative(path: Path) -> Path:
    try:
        return path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return path.resolve()


def stable_split(key: str, split: tuple[float, float, float]) -> str:
    train_ratio, val_ratio, _test_ratio = split
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16) / 0xFFFFFFFF
    if value < train_ratio:
        return "train"
    if value < train_ratio + val_ratio:
        return "val"
    return "test"


def clamp_bbox(bbox: tuple[float, float, float, float], width: int, height: int) -> tuple[float, float, float, float] | None:
    x1, y1, x2, y2 = bbox
    x1 = max(0.0, min(float(width), x1))
    y1 = max(0.0, min(float(height), y1))
    x2 = max(0.0, min(float(width), x2))
    y2 = max(0.0, min(float(height), y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def rect_to_polygon(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float, float, float, float, float]:
    x1, y1, x2, y2 = bbox
    return x1, y1, x2, y1, x2, y2, x1, y2


def polygon_bbox(polygon: tuple[float, float, float, float, float, float, float, float]) -> tuple[float, float, float, float]:
    xs = polygon[0::2]
    ys = polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def values_to_polygon(values: list[float]) -> tuple[float, float, float, float, float, float, float, float] | None:
    if len(values) < 4:
        return None
    if len(values) == 4:
        x1, y1, x2, y2 = values
        return rect_to_polygon((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    if len(values) >= 8:
        return tuple(values[:8])  # type: ignore[return-value]
    return None


def yolo_obb_line(class_id: int, polygon: tuple[float, float, float, float, float, float, float, float], tile_size: int) -> str | None:
    bbox = polygon_bbox(polygon)
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    normalized = []
    for index, value in enumerate(polygon):
        norm = value / tile_size
        if norm < 0.0 or norm > 1.0:
            return None
        normalized.append(norm)
    if polygon_area(polygon) <= 0:
        return None
    coords = " ".join(f"{value:.6f}" for value in normalized)
    return f"{class_id} {coords}"


def polygon_area(polygon: tuple[float, float, float, float, float, float, float, float]) -> float:
    points = list(zip(polygon[0::2], polygon[1::2]))
    total = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def polygon_inside_tile(
    polygon: tuple[float, float, float, float, float, float, float, float],
    tile_bbox: tuple[int, int, int, int],
) -> bool:
    x1, y1, x2, y2 = tile_bbox
    points = list(zip(polygon[0::2], polygon[1::2]))
    return all(x1 <= px <= x2 and y1 <= py <= y2 for px, py in points)


def shift_polygon(
    polygon: tuple[float, float, float, float, float, float, float, float],
    x_offset: int,
    y_offset: int,
) -> tuple[float, float, float, float, float, float, float, float]:
    shifted = []
    for index, value in enumerate(polygon):
        shifted.append(value - (x_offset if index % 2 == 0 else y_offset))
    return tuple(shifted)  # type: ignore[return-value]


def find_images(root: Path) -> dict[str, Path]:
    images: dict[str, Path] = {}
    if not root.exists():
        return images
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            images.setdefault(path.name, path)
            images.setdefault(path.stem, path)
    return images


def image_lookup(path: Path, images: dict[str, Path]) -> Path | None:
    return images.get(path.name) or images.get(path.stem) or images.get(path.with_suffix("").name)


def is_archive(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in ARCHIVE_SUFFIXES or name.endswith((".tar.gz", ".tar.bz2", ".tar.xz"))


def archive_destination_name(path: Path) -> str:
    name = path.name
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".zip", ".tgz", ".tar", ".gz", ".bz2", ".xz"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def parse_number_list(value: Any) -> list[float]:
    if isinstance(value, str):
        value = value.replace(";", ",").replace(" ", ",")
        return [float(part) for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        flat: list[float] = []
        for item in value:
            if isinstance(item, (list, tuple)):
                flat.extend(parse_number_list(item))
            else:
                flat.append(float(item))
        return flat
    return []


def rotated_box_to_polygon(
    cx: float, cy: float, w: float, h: float, angle: float
) -> tuple[float, float, float, float, float, float, float, float]:
    """Convert (center, size, angle in radians) to a 4-corner polygon."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx, dy = w / 2.0, h / 2.0
    corners = ((-dx, -dy), (dx, -dy), (dx, dy), (-dx, dy))
    flat: list[float] = []
    for x, y in corners:
        flat.append(cx + x * cos_a - y * sin_a)
        flat.append(cy + x * sin_a + y * cos_a)
    return tuple(flat)  # type: ignore[return-value]


def ensure_yolo_dirs(yolo_root: Path, clean: bool) -> None:
    if clean and yolo_root.exists():
        shutil.rmtree(yolo_root)
    for split in ("train", "val", "test"):
        (yolo_root / split / "images").mkdir(parents=True, exist_ok=True)
        (yolo_root / split / "labels").mkdir(parents=True, exist_ok=True)


def write_data_yaml(yolo_root: Path, registry: ClassRegistry) -> None:
    names = [name.replace("'", "") for name in registry.names]
    lines = [
        f"path: {yolo_root.resolve().as_posix()}",
        "task: obb",
        "train: train/images",
        "val: val/images",
        "test: test/images",
        f"nc: {len(names)}",
        "names:",
    ]
    lines.extend(f"  {idx}: {name}" for idx, name in enumerate(names))
    (yolo_root / "data.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    registry.save(yolo_root / "classes.json")


def convert_image_to_tiles(
    dataset: str,
    image_path: Path,
    annotations: list[Annotation],
    yolo_root: Path,
    registry: ClassRegistry,
    split: tuple[float, float, float],
    tile_size: int,
    overlap: float,
    min_visibility: float,
    include_empty_ratio: float,
    manifest,
) -> tuple[int, int]:
    try:
        image = Image.open(image_path)
        image = ImageOps.exif_transpose(image)
        if image.mode != "RGB":
            image = image.convert("RGB")
    except Exception as exc:
        print(f"WARNING: unable to open {image_path}: {exc}", file=sys.stderr)
        return 0, 0

    width, height = image.size
    valid_annotations = [
        ann
        for ann in annotations
        if clamp_bbox(ann.bbox, width, height) is not None
    ]

    stride = max(1, int(tile_size * (1.0 - overlap)))
    x_starts = tile_starts(width, tile_size, stride)
    y_starts = tile_starts(height, tile_size, stride)

    tiles_written = 0
    labels_written = 0
    split_name = stable_split(f"{dataset}:{image_path.as_posix()}", split)

    for y0 in y_starts:
        for x0 in x_starts:
            tile_bbox = (x0, y0, min(x0 + tile_size, width), min(y0 + tile_size, height))
            label_lines: list[str] = []
            for ann in valid_annotations:
                intersection = intersect_bbox(ann.bbox, tile_bbox)
                if not intersection:
                    continue
                ann_area = area(ann.bbox)
                if ann_area <= 0 or area(intersection) / ann_area < min_visibility:
                    continue
                if not polygon_inside_tile(ann.polygon, tile_bbox):
                    continue
                local = shift_polygon(ann.polygon, x0, y0)
                line = yolo_obb_line(registry.add(ann.label), local, tile_size)
                if line:
                    label_lines.append(line)

            if not label_lines and include_empty_ratio <= 0:
                continue
            if not label_lines and include_empty_ratio < 1:
                keep_value = int(hashlib.sha1(f"{image_path}:{x0}:{y0}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
                if keep_value > include_empty_ratio:
                    continue

            tile = Image.new("RGB", (tile_size, tile_size), (0, 0, 0))
            crop = image.crop(tile_bbox)
            tile.paste(crop, (0, 0))

            safe_stem = sanitize_label(f"{dataset}_{image_path.stem}_{x0}_{y0}")
            image_out = yolo_root / split_name / "images" / f"{safe_stem}.jpg"
            label_out = yolo_root / split_name / "labels" / f"{safe_stem}.txt"
            tile.save(image_out, quality=94)
            label_out.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
            manifest.write(json.dumps({
                "dataset": dataset,
                "split": split_name,
                "source_image": str(repo_relative(image_path)),
                "image": str(repo_relative(image_out)),
                "label": str(repo_relative(label_out)),
                "tile": [x0, y0, tile_bbox[2] - x0, tile_bbox[3] - y0],
                "labels": len(label_lines),
            }) + "\n")
            tiles_written += 1
            labels_written += len(label_lines)

    return tiles_written, labels_written


def tile_starts(length: int, tile_size: int, stride: int) -> list[int]:
    if length <= tile_size:
        return [0]
    starts = list(range(0, max(1, length - tile_size + 1), stride))
    last = length - tile_size
    if starts[-1] != last:
        starts.append(last)
    return starts


def intersect_bbox(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float] | None:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def process_xview(raw_root: Path) -> dict[Path, list[Annotation]]:
    normalize_xview_layout(raw_root)
    geojson_candidates = (
        list(raw_root.rglob("*train*.geojson"))
        + list(raw_root.rglob("*xview*.geojson"))
        + list(raw_root.rglob("*.geojson"))
    )
    if not geojson_candidates:
        print("xView: no GeoJSON labels found")
        print(f"Expected train_labels archive extracted under {raw_root}, containing xView_train.geojson.")
        return {}
    geojson_path = geojson_candidates[0]
    images = find_images(raw_root)
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    grouped: dict[Path, list[Annotation]] = {}
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        image_id = str(props.get("image_id") or props.get("IMAGE_ID") or "")
        image_path = image_lookup(Path(image_id), images)
        if not image_path:
            continue
        type_id = props.get("type_id", props.get("TYPE_ID"))
        try:
            type_id_int = int(type_id)
        except (TypeError, ValueError):
            type_id_int = -1
        if type_id_int in {75, 82}:
            continue
        label = XVIEW_TYPE_ID_TO_NAME.get(type_id_int, f"xview_type_{type_id_int}")
        polygon = None
        for key in ("bounds_imcoords", "BOUNDS_IMCOORDS"):
            if props.get(key):
                polygon = values_to_polygon(parse_number_list(props[key]))
                break
        if polygon is None:
            coords = feature.get("geometry", {}).get("coordinates", [])
            polygon = values_to_polygon(parse_number_list(coords))
        if polygon:
            grouped.setdefault(image_path, []).append(Annotation(label, polygon, "xview"))
    return grouped


def parse_dota_line(line: str, image_size: tuple[int, int] | None) -> Annotation | None:
    parts = line.strip().lstrip("\ufeff").split()
    if len(parts) < 9:
        return None
    if parts[0].lower().startswith(("imagesource", "gsd")):
        return None

    try:
        class_id = int(parts[0])
        coords = [float(part) for part in parts[1:9]]
    except ValueError:
        class_id = -1
        coords = []

    if image_size and class_id >= 0 and len(coords) == 8 and all(0.0 <= value <= 1.0 for value in coords):
        width, height = image_size
        scaled = [value * (width if index % 2 == 0 else height) for index, value in enumerate(coords)]
        label = DOTA_CLASSES[class_id] if 0 <= class_id < len(DOTA_CLASSES) else f"dota_class_{class_id}"
        polygon = values_to_polygon(scaled)
        return Annotation(label, polygon, "dota") if polygon else None

    try:
        coords = [float(part) for part in parts[:8]]
    except ValueError:
        return None
    label = f"dota_{sanitize_label(parts[8])}"
    polygon = values_to_polygon(coords)
    return Annotation(label, polygon, "dota") if polygon else None


def parse_point_pairs(value: Any) -> list[float]:
    if not isinstance(value, list):
        return parse_number_list(value)
    flat: list[float] = []
    for item in value:
        if isinstance(item, dict):
            if item.get("x") is not None and item.get("y") is not None:
                flat.extend([float(item["x"]), float(item["y"])])
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            flat.extend(parse_number_list(item[:2]))
        else:
            flat.extend(parse_number_list(item))
    return flat


def parse_dota_json(json_path: Path) -> list[Annotation]:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    annotations: list[Annotation] = []
    for obj in data.get("objects", []):
        if not isinstance(obj, dict):
            continue
        label_value = (
            obj.get("classTitle")
            or obj.get("class_name")
            or obj.get("className")
            or obj.get("label")
            or obj.get("name")
            or obj.get("category")
            or "unknown"
        )
        points_value = obj.get("points") or obj.get("polygon") or obj.get("bbox") or obj.get("bndbox")
        if isinstance(points_value, dict):
            points_value = (
                points_value.get("exterior")
                or points_value.get("points")
                or points_value.get("vertices")
                or points_value.get("bbox")
            )
        polygon = values_to_polygon(parse_point_pairs(points_value))
        if polygon:
            annotations.append(Annotation(f"dota_{sanitize_label(label_value)}", polygon, "dota"))
    return annotations


def dota_label_files(raw_root: Path) -> list[Path]:
    annotation_dirs = [
        path for path in raw_root.rglob("*")
        if path.is_dir()
        and (path.name.lower() in {"ann", "anns", "annotation", "annotations", "labeltxt"}
             or "label" in path.name.lower())
    ]
    files: list[Path] = []
    for directory in annotation_dirs:
        files.extend(directory.rglob("*.txt"))
        files.extend(directory.rglob("*.json"))
    if not files:
        files = list(raw_root.rglob("*.txt"))
        files.extend(raw_root.rglob("*.json"))
    return sorted(set(files))


def print_dota_empty_diagnostics(
    raw_root: Path,
    images: dict[str, Path],
    annotation_files: list[Path],
    matched_labels: int,
    parsed_annotations: int,
) -> None:
    unique_images = sorted({path for path in images.values()})
    print("DOTA: no usable image/label pairs found")
    print(f"  raw root: {raw_root}")
    print(f"  images found: {len(unique_images)}")
    print(f"  annotation files found: {len(annotation_files)}")
    print(f"  annotation files matched to images: {matched_labels}")
    print(f"  parsed annotations: {parsed_annotations}")
    if unique_images:
        print("  sample images:")
        for path in unique_images[:5]:
            print(f"    {path.relative_to(raw_root) if path.is_relative_to(raw_root) else path}")
    if annotation_files:
        print("  sample annotation files:")
        for path in annotation_files[:5]:
            print(f"    {path.relative_to(raw_root) if path.is_relative_to(raw_root) else path}")


def process_dota(raw_root: Path) -> dict[Path, list[Annotation]]:
    extract_nested_archives(raw_root)
    images = find_images(raw_root)
    label_files = dota_label_files(raw_root)
    grouped: dict[Path, list[Annotation]] = {}
    matched_labels = 0
    parsed_annotations = 0
    for label_path in label_files:
        image_path = image_lookup(label_path, images)
        if not image_path:
            continue
        matched_labels += 1
        image_size = None
        try:
            with Image.open(image_path) as image:
                image_size = image.size
        except Exception:
            pass
        if label_path.suffix.lower() == ".json":
            annotations = parse_dota_json(label_path)
            parsed_annotations += len(annotations)
        else:
            annotations = []
            for line in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                annotation = parse_dota_line(line, image_size)
                if annotation:
                    annotations.append(annotation)
                    parsed_annotations += 1
        if annotations:
            grouped[image_path] = annotations
    if not grouped:
        print_dota_empty_diagnostics(raw_root, images, label_files, matched_labels, parsed_annotations)
    return grouped


def process_fair1m(raw_root: Path) -> dict[Path, list[Annotation]]:
    images = find_images(raw_root)
    xml_files = list(raw_root.rglob("*.xml"))
    grouped: dict[Path, list[Annotation]] = {}
    matched_xml = 0
    parsed_annotations = 0
    for xml_path in xml_files:
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue
        filename_node = root.find(".//source/filename")
        if filename_node is None:
            filename_node = root.find(".//filename")
        image_name = filename_node.text.strip() if filename_node is not None and filename_node.text else xml_path.stem
        image_path = image_lookup(Path(image_name), images) or image_lookup(xml_path, images)
        if not image_path:
            continue
        matched_xml += 1
        annotations: list[Annotation] = []
        for obj in root.findall(".//object"):
            name_node = obj.find(".//possibleresult/name")
            if name_node is None:
                name_node = obj.find("name")
            label = f"fair1m_{sanitize_label(name_node.text if name_node is not None else 'unknown')}"
            points = []
            for point in obj.findall(".//point"):
                points.extend(parse_number_list(point.text or ""))
            if not points:
                for tag in ("bndbox", "robndbox"):
                    node = obj.find(f".//{tag}")
                    if node is not None:
                        points.extend(parse_number_list([child.text for child in node if child.text]))
            polygon = values_to_polygon(points)
            if polygon:
                annotations.append(Annotation(label, polygon, "fair1m"))
                parsed_annotations += 1
        if annotations:
            grouped[image_path] = annotations
    if not grouped:
        unique_images = sorted({path for path in images.values()})
        print("FAIR1M: no usable image/XML pairs found")
        print(f"  raw root: {raw_root}")
        print(f"  images found: {len(unique_images)}")
        print(f"  XML files found: {len(xml_files)}")
        print(f"  XML files matched to images: {matched_xml}")
        print(f"  parsed annotations: {parsed_annotations}")
        if unique_images:
            print("  sample images:")
            for path in unique_images[:5]:
                print(f"    {path.relative_to(raw_root) if path.is_relative_to(raw_root) else path}")
        if xml_files:
            print("  sample XML files:")
            for path in xml_files[:5]:
                print(f"    {path.relative_to(raw_root) if path.is_relative_to(raw_root) else path}")
    return grouped


def process_coco(raw_root: Path, dataset_prefix: str) -> dict[Path, list[Annotation]]:
    json_files = [p for p in raw_root.rglob("*.json") if "coco" in p.name.lower() or "annotation" in p.name.lower()]
    if not json_files:
        json_files = list(raw_root.rglob("*.json"))
    images_by_key = find_images(raw_root)
    grouped: dict[Path, list[Annotation]] = {}
    for json_path in json_files:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not all(key in data for key in ("images", "annotations", "categories")):
            continue
        categories = {
            item.get("id"): f"{dataset_prefix}_{sanitize_label(item.get('name', item.get('id')))}"
            for item in data.get("categories", [])
        }
        image_id_to_path: dict[Any, Path] = {}
        for image in data.get("images", []):
            name = Path(str(image.get("file_name", ""))).name
            path = images_by_key.get(name) or images_by_key.get(Path(name).stem)
            if path:
                image_id_to_path[image.get("id")] = path
        for ann in data.get("annotations", []):
            image_path = image_id_to_path.get(ann.get("image_id"))
            bbox = ann.get("bbox")
            if not image_path or not bbox or len(bbox) < 4:
                continue
            x, y, w, h = [float(v) for v in bbox[:4]]
            label = categories.get(ann.get("category_id"), f"{dataset_prefix}_category_{ann.get('category_id')}")
            grouped.setdefault(image_path, []).append(Annotation(label, rect_to_polygon((x, y, x + w, y + h)), dataset_prefix))
        if grouped:
            break
    return grouped


def _xml_text(node: ET.Element | None, *names: str) -> str | None:
    if node is None:
        return None
    for name in names:
        child = node.find(name)
        if child is not None and child.text and child.text.strip():
            return child.text.strip()
    return None


def _xml_float(node: ET.Element | None, *names: str) -> float | None:
    text = _xml_text(node, *names)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_dior_object(obj: ET.Element) -> Annotation | None:
    name_text = _xml_text(obj, "name", "n", "category", "class")
    if not name_text:
        return None
    label = f"dior_{sanitize_label(name_text)}"

    robndbox = obj.find("robndbox")
    if robndbox is not None:
        cx = _xml_float(robndbox, "cx", "x_ctr")
        cy = _xml_float(robndbox, "cy", "y_ctr")
        w = _xml_float(robndbox, "w", "width")
        h = _xml_float(robndbox, "h", "height")
        angle = _xml_float(robndbox, "angle", "theta")
        if None not in (cx, cy, w, h, angle):
            return Annotation(label, rotated_box_to_polygon(cx, cy, w, h, angle), "dior")

        corner_keys = (
            ("x_left_top", "y_left_top"),
            ("x_right_top", "y_right_top"),
            ("x_right_bottom", "y_right_bottom"),
            ("x_left_bottom", "y_left_bottom"),
        )
        flat: list[float] = []
        for kx, ky in corner_keys:
            x = _xml_float(robndbox, kx)
            y = _xml_float(robndbox, ky)
            if x is None or y is None:
                flat = []
                break
            flat.extend([x, y])
        if len(flat) == 8:
            polygon = values_to_polygon(flat)
            if polygon:
                return Annotation(label, polygon, "dior")

    bndbox = obj.find("bndbox")
    if bndbox is not None:
        xmin = _xml_float(bndbox, "xmin")
        ymin = _xml_float(bndbox, "ymin")
        xmax = _xml_float(bndbox, "xmax")
        ymax = _xml_float(bndbox, "ymax")
        if None not in (xmin, ymin, xmax, ymax) and xmax > xmin and ymax > ymin:
            return Annotation(label, rect_to_polygon((xmin, ymin, xmax, ymax)), "dior")

    return None


def _parse_yaml_class_names(text: str) -> list[str]:
    """Minimal parser for the names: field of an Ultralytics data.yaml."""
    dict_matches = re.findall(r"^\s*(\d+)\s*:\s*([^\s][^\n]*)$", text, re.M)
    if dict_matches:
        items = sorted(((int(k), v.strip().strip("'\"")) for k, v in dict_matches), key=lambda x: x[0])
        return [v for _, v in items]
    list_match = re.search(r"^\s*names\s*:\s*\[([^\]]+)\]", text, re.M)
    if list_match:
        return [s.strip().strip("'\"") for s in list_match.group(1).split(",") if s.strip()]
    return []


def _load_dior_class_names(raw_root: Path) -> list[str]:
    """Resolve DIOR class names from classes.txt (preferred) or data.yaml."""
    for classes_txt in raw_root.rglob("classes.txt"):
        try:
            lines = [line.strip() for line in classes_txt.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                return lines
        except OSError:
            continue
    for data_yaml in raw_root.rglob("data.yaml"):
        try:
            names = _parse_yaml_class_names(data_yaml.read_text(encoding="utf-8"))
            if names:
                return names
        except OSError:
            continue
    return []


def _process_dior_yolo_obb(
    raw_root: Path,
    label_files: list[Path],
    images: dict[str, Path],
) -> dict[Path, list[Annotation]]:
    class_names = _load_dior_class_names(raw_root)
    grouped: dict[Path, list[Annotation]] = {}
    matched = 0
    parsed = 0
    for label_path in label_files:
        image_path = image_lookup(label_path, images)
        if not image_path:
            continue
        matched += 1
        try:
            with Image.open(image_path) as img:
                img_w, img_h = img.size
        except Exception:
            continue
        annotations: list[Annotation] = []
        for line in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.strip().split()
            if len(parts) != 9:
                continue
            try:
                class_id = int(parts[0])
                coords = [float(p) for p in parts[1:9]]
            except ValueError:
                continue
            if any(c < 0 or c > 1 for c in coords):
                continue
            if 0 <= class_id < len(class_names):
                label = f"dior_{sanitize_label(class_names[class_id])}"
            else:
                label = f"dior_class_{class_id}"
            pixel = [
                value * (img_w if index % 2 == 0 else img_h)
                for index, value in enumerate(coords)
            ]
            polygon = values_to_polygon(pixel)
            if polygon:
                annotations.append(Annotation(label, polygon, "dior"))
                parsed += 1
        if annotations:
            grouped[image_path] = annotations

    if not grouped:
        print("DIOR: no usable image/label.txt pairs found")
        print(f"  raw root: {raw_root}")
        print(f"  images found: {len(set(images.values()))}")
        print(f"  classes.txt names loaded: {len(class_names)}")
        print(f"  txt files: {len(label_files)}")
        print(f"  txt files matched to images: {matched}")
        print(f"  parsed annotations: {parsed}")
    return grouped


def _process_dior_xml(
    raw_root: Path,
    images: dict[str, Path],
) -> dict[Path, list[Annotation]]:
    grouped: dict[Path, list[Annotation]] = {}
    matched_xml = 0
    parsed_annotations = 0
    xml_files = list(raw_root.rglob("*.xml"))
    for xml_path in xml_files:
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue
        filename = _xml_text(root, "filename") or xml_path.stem
        image_path = image_lookup(Path(filename), images) or image_lookup(xml_path, images)
        if not image_path:
            continue
        matched_xml += 1
        annotations: list[Annotation] = []
        for obj in root.findall(".//object"):
            ann = _parse_dior_object(obj)
            if ann:
                annotations.append(ann)
                parsed_annotations += 1
        if annotations:
            grouped[image_path] = annotations

    if not grouped:
        unique_images = sorted({path for path in images.values()})
        print("DIOR: no usable image/XML pairs found")
        print(f"  raw root: {raw_root}")
        print(f"  images found: {len(unique_images)}")
        print(f"  XML files found: {len(xml_files)}")
        print(f"  XML files matched to images: {matched_xml}")
        print(f"  parsed annotations: {parsed_annotations}")
    return grouped


def process_dior(raw_root: Path) -> dict[Path, list[Annotation]]:
    extract_nested_archives(raw_root)
    images = find_images(raw_root)

    # Prefer YOLO-OBB TXT format (the Kaggle DIOR-R distribution).
    # Only walk directories that are actually labels (avoids picking up classes.txt etc.).
    yolo_label_files: list[Path] = []
    for label_dir in raw_root.rglob("*"):
        if label_dir.is_dir() and label_dir.name.lower() in ("labels", "labeltxt"):
            yolo_label_files.extend(label_dir.rglob("*.txt"))
    if yolo_label_files:
        return _process_dior_yolo_obb(raw_root, yolo_label_files, images)

    # Fall back to PASCAL VOC XML (the original DIOR-R distribution from IEEE DataPort).
    return _process_dior_xml(raw_root, images)


SODAA_IGNORE_LABELS = {"ignore", "ignored_region", "ignored", "ignore_region"}


def _process_sodaa_coco_json(
    data: dict,
    images_by_key: dict[str, Path],
    fallback_stem: str | None = None,
) -> dict[Path, list[Annotation]]:
    grouped: dict[Path, list[Annotation]] = {}
    categories = {
        item.get("id"): sanitize_label(item.get("name") or item.get("id"))
        for item in data.get("categories", []) or []
    }
    image_id_to_path: dict[Any, Path] = {}
    for index, image in enumerate(data.get("images", []) or []):
        if isinstance(image, dict):
            image_id = image.get("id", index)
            file_name = image.get("file_name") or image.get("filename") or ""
            name = Path(str(file_name)).name if file_name else ""
        elif isinstance(image, str):
            image_id = index
            name = Path(image).name
        else:
            continue
        path = (images_by_key.get(name) or images_by_key.get(Path(name).stem)) if name else None
        if not path and fallback_stem:
            path = (
                images_by_key.get(f"{fallback_stem}.jpg")
                or images_by_key.get(f"{fallback_stem}.png")
                or images_by_key.get(f"{fallback_stem}.tif")
                or images_by_key.get(fallback_stem)
            )
        if not path and image_id is not None:
            stem = str(image_id).zfill(5)
            path = images_by_key.get(stem) or images_by_key.get(f"{stem}.jpg")
        if path:
            image_id_to_path[image_id] = path

    fallback_path: Path | None = None
    if fallback_stem and not image_id_to_path:
        fallback_path = (
            images_by_key.get(f"{fallback_stem}.jpg")
            or images_by_key.get(f"{fallback_stem}.png")
            or images_by_key.get(f"{fallback_stem}.tif")
            or images_by_key.get(fallback_stem)
        )

    for ann in data.get("annotations", []) or []:
        category_name = categories.get(ann.get("category_id"))
        if not category_name or category_name in SODAA_IGNORE_LABELS:
            continue
        image_path = image_id_to_path.get(ann.get("image_id")) or fallback_path
        if not image_path:
            continue
        polygon = None
        poly = ann.get("poly")
        if poly:
            polygon = values_to_polygon(parse_number_list(poly))
        if polygon is None:
            bbox = ann.get("bbox")
            if bbox and len(bbox) >= 4:
                x, y, w, h = [float(v) for v in bbox[:4]]
                polygon = rect_to_polygon((x, y, x + w, y + h))
        if polygon:
            grouped.setdefault(image_path, []).append(
                Annotation(f"sodaa_{category_name}", polygon, "sodaa")
            )
    return grouped


def _process_sodaa_per_image_json(
    json_path: Path,
    images_by_key: dict[str, Path],
) -> tuple[Path | None, list[Annotation]]:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, []
    if not isinstance(data, dict):
        return None, []
    image_path = (
        image_lookup(Path(str(data.get("file_name") or json_path.stem)), images_by_key)
        or image_lookup(json_path, images_by_key)
    )
    if not image_path:
        return None, []
    annotations: list[Annotation] = []
    for ann in data.get("annotations", []) or []:
        category_name = sanitize_label(ann.get("category") or ann.get("category_name") or "")
        if not category_name or category_name in SODAA_IGNORE_LABELS:
            continue
        polygon = None
        poly = ann.get("poly")
        if poly:
            polygon = values_to_polygon(parse_number_list(poly))
        if polygon is None:
            bbox = ann.get("bbox")
            if bbox and len(bbox) >= 4:
                x, y, w, h = [float(v) for v in bbox[:4]]
                polygon = rect_to_polygon((x, y, x + w, y + h))
        if polygon:
            annotations.append(Annotation(f"sodaa_{category_name}", polygon, "sodaa"))
    return image_path, annotations


def process_sodaa(raw_root: Path) -> dict[Path, list[Annotation]]:
    extract_nested_archives(raw_root)
    images = find_images(raw_root)
    grouped: dict[Path, list[Annotation]] = {}
    parsed_files = 0
    for json_path in raw_root.rglob("*.json"):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        if all(key in data for key in ("images", "annotations", "categories")):
            split = _process_sodaa_coco_json(data, images, fallback_stem=json_path.stem)
            for path, anns in split.items():
                grouped.setdefault(path, []).extend(anns)
            if split:
                parsed_files += 1
            continue
        if "annotations" in data and isinstance(data["annotations"], list):
            image_path, anns = _process_sodaa_per_image_json(json_path, images)
            if image_path and anns:
                grouped.setdefault(image_path, []).extend(anns)
                parsed_files += 1

    if not grouped:
        print("SODA-A: no usable image/JSON pairs found")
        print(f"  raw root: {raw_root}")
        print(f"  images found: {len(set(images.values()))}")
        print(f"  JSON files parsed: {parsed_files}")
    return grouped


def process_hrsc(raw_root: Path) -> dict[Path, list[Annotation]]:
    extract_nested_archives(raw_root)
    images = find_images(raw_root)
    grouped: dict[Path, list[Annotation]] = {}
    matched_xml = 0
    parsed_annotations = 0
    xml_files = [p for p in raw_root.rglob("*.xml") if p.name.lower() != "sysdata.xml"]
    for xml_path in xml_files:
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue
        filename = _xml_text(root, "Img_FileName", "filename")
        ext = _xml_text(root, "Img_FileFmt") or ""
        candidate = filename
        if filename and ext and not Path(filename).suffix:
            candidate = f"{filename}.{ext.lstrip('.')}"
        image_path = (
            (image_lookup(Path(candidate), images) if candidate else None)
            or image_lookup(xml_path, images)
        )
        if not image_path:
            continue
        matched_xml += 1
        annotations: list[Annotation] = []
        for obj in root.findall(".//HRSC_Object"):
            class_id = _xml_text(obj, "Class_ID")
            cx = _xml_float(obj, "mbox_cx")
            cy = _xml_float(obj, "mbox_cy")
            w = _xml_float(obj, "mbox_w")
            h = _xml_float(obj, "mbox_h")
            angle = _xml_float(obj, "mbox_ang")
            if not class_id or None in (cx, cy, w, h, angle):
                continue
            label = f"hrsc_{sanitize_label(class_id)}"
            polygon = rotated_box_to_polygon(cx, cy, w, h, angle)
            annotations.append(Annotation(label, polygon, "hrsc2016"))
            parsed_annotations += 1
        if annotations:
            grouped[image_path] = annotations

    if not grouped:
        print("HRSC2016: no usable image/XML pairs found")
        print(f"  raw root: {raw_root}")
        print(f"  images found: {len(set(images.values()))}")
        print(f"  XML files found: {len(xml_files)}")
        print(f"  XML files matched to images: {matched_xml}")
        print(f"  parsed annotations: {parsed_annotations}")
    return grouped


PROCESSORS = {
    "xview": process_xview,
    "dota": process_dota,
    "rareplanes": lambda root: process_coco(root, "rareplanes"),
    "fair1m": process_fair1m,
    "dior": process_dior,
    "sodaa": process_sodaa,
    "hrsc2016": process_hrsc,
}


def balance_annotations_by_class(
    dataset: str,
    grouped: dict[Path, list[Annotation]],
    max_per_class: int,
) -> tuple[dict[Path, list[Annotation]], dict[str, tuple[int, int]]]:
    """Deterministically cap per-class instances within a dataset's annotations.

    Returns the filtered grouping and a per-class report of (kept, original) counts.
    Subsampling is stable across runs (SHA1 of dataset + image + index + label).
    """
    counts: dict[str, int] = {}
    for anns in grouped.values():
        for ann in anns:
            counts[ann.label] = counts.get(ann.label, 0) + 1

    if max_per_class <= 0 or not counts:
        return grouped, {label: (count, count) for label, count in counts.items()}

    keep_rate = {
        label: 1.0 if count <= max_per_class else max_per_class / count
        for label, count in counts.items()
    }
    if all(rate >= 1.0 for rate in keep_rate.values()):
        return grouped, {label: (count, count) for label, count in counts.items()}

    filtered: dict[Path, list[Annotation]] = {}
    kept_counts: dict[str, int] = {label: 0 for label in counts}
    for path, anns in grouped.items():
        kept: list[Annotation] = []
        for index, ann in enumerate(anns):
            rate = keep_rate.get(ann.label, 1.0)
            if rate >= 1.0:
                kept.append(ann)
                kept_counts[ann.label] += 1
                continue
            key = f"{dataset}:{path.as_posix()}:{index}:{ann.label}"
            score = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
            if score < rate:
                kept.append(ann)
                kept_counts[ann.label] += 1
        if kept:
            filtered[path] = kept

    report = {label: (kept_counts[label], counts[label]) for label in counts}
    return filtered, report


def convert_dataset(
    dataset: str,
    raw_root: Path,
    yolo_root: Path,
    registry: ClassRegistry,
    split: tuple[float, float, float],
    tile_size: int,
    overlap: float,
    min_visibility: float,
    include_empty_ratio: float,
    max_instances_per_class: int,
    manifest,
) -> ConversionStats:
    processor = PROCESSORS[dataset]
    grouped = processor(raw_root)
    grouped, balance_report = balance_annotations_by_class(dataset, grouped, max_instances_per_class)
    if max_instances_per_class > 0:
        capped = [
            f"{label}: {kept}/{original}"
            for label, (kept, original) in sorted(balance_report.items())
            if kept != original
        ]
        if capped:
            print(f"{dataset}: capped per-class instances at {max_instances_per_class}: " + ", ".join(capped))
    stats = ConversionStats(dataset=dataset, images_seen=len(grouped))
    for image_path, annotations in tqdm(grouped.items(), desc=f"convert:{dataset}"):
        tiles, labels = convert_image_to_tiles(
            dataset,
            image_path,
            annotations,
            yolo_root,
            registry,
            split,
            tile_size,
            overlap,
            min_visibility,
            include_empty_ratio,
            manifest,
        )
        if tiles:
            stats.images_written += 1
        else:
            stats.skipped += 1
        stats.tiles_written += tiles
        stats.labels_written += labels
    return stats


def download_http(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        print(f"exists: {destination}")
        return
    request = Request(url, headers={"User-Agent": "SentinelOS dataset preparer"})
    with urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        total = int(response.headers.get("Content-Length", "0") or 0)
        with tqdm(total=total, unit="B", unit_scale=True, desc=destination.name) as pbar:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                pbar.update(len(chunk))


def extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(destination)
    elif is_archive(archive):
        with tarfile.open(archive, "r:*") as tf:
            tf.extractall(destination)


def extract_nested_archives(root: Path, archive_names: set[str] | None = None) -> None:
    for archive in [path for path in root.rglob("*") if path.is_file() and is_archive(path)]:
        if archive_names and archive.name not in archive_names:
            continue
        marker = archive.with_suffix(archive.suffix + ".extracted")
        if marker.exists():
            continue
        destination = archive.parent / archive_destination_name(archive)
        print(f"Extracting nested archive {archive.name} -> {destination}")
        extract_archive(archive, destination)
        marker.write_text("ok\n", encoding="utf-8")


def normalize_xview_layout(raw_root: Path) -> None:
    extract_nested_archives(raw_root, XVIEW_ARCHIVES)

    for archive_name in XVIEW_ARCHIVES:
        archive = raw_root / archive_name
        if not archive.exists():
            continue
        marker = archive.with_suffix(archive.suffix + ".extracted")
        if marker.exists():
            continue
        extract_archive(archive, raw_root / archive_destination_name(archive))
        marker.write_text("ok\n", encoding="utf-8")


def run_aws_sync(bucket_uri: str, destination: Path, extra_args: list[str] | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    command = ["aws", "s3", "sync", "--no-sign-request", bucket_uri, str(destination)]
    command.extend(extra_args or [])
    subprocess.run(command, check=True)


def download_dataset(dataset: str, raw_root: Path, args: argparse.Namespace) -> None:
    raw_root.mkdir(parents=True, exist_ok=True)
    if dataset == "xview":
        print("xView requires registration/terms acceptance. Download from https://xviewdataset.org/ and place files under:")
        print(f"  {raw_root}")
        print("Expected archives: train_images, train_labels, and val_images as .tgz, .tar.gz, .tar, or .zip.")
        return
    if dataset == "dota":
        print("DOTA downloads are distributed from the official DOTA site and mirrors. Place extracted images and labelTxt under:")
        print(f"  {raw_root}")
        return
    if dataset == "rareplanes":
        bucket = "s3://rareplanes-public"
        print(f"Syncing RarePlanes from {bucket}.")
        run_aws_sync(bucket, raw_root, args.aws_extra)
        return
    if dataset == "fair1m":
        print("FAIR1M is commonly mirrored on Hugging Face and official challenge mirrors. Best-effort Hugging Face download:")
        command = ["huggingface-cli", "download", "blanchon/FAIR1M", "--repo-type", "dataset", "--local-dir", str(raw_root)]
        subprocess.run(command, check=True)
        return
    if dataset == "dior":
        print("DIOR-R is distributed from IEEE DataPort and Kaggle (YOLOv11-OBB-format community mirror).")
        print("Place the extracted dataset (Annotations/Oriented Bounding Boxes/*.xml + JPEGImages/*.jpg) or the raw dior.zip under:")
        print(f"  {raw_root}")
        return
    if dataset == "sodaa":
        print("SODA-A is distributed from https://shaunyuan22.github.io/SODA/. Place the extracted dataset")
        print("(Images/{train,val,test}/*.jpg + Annotations/*.json) or sodaa.zip under:")
        print(f"  {raw_root}")
        return
    if dataset == "hrsc2016":
        print("HRSC2016 is distributed from IEEE DataPort and community mirrors. Place the extracted dataset")
        print("(Train/AllImages/*.bmp + Train/Annotations/*.xml, plus Test/ and Val/) or HRSC2016_dataset.zip under:")
        print(f"  {raw_root}")
        return
    raise ValueError(f"Unsupported dataset: {dataset}")


def import_archives(dataset: str, raw_root: Path, archives: list[str]) -> None:
    raw_root.mkdir(parents=True, exist_ok=True)
    for item in archives:
        source = Path(item)
        if source.is_dir():
            target = raw_root / source.name
            if target.exists():
                continue
            shutil.copytree(source, target)
            continue
        if not source.exists():
            parsed = urlparse(item)
            if parsed.scheme in {"http", "https"}:
                source = raw_root / Path(parsed.path).name
                download_http(item, source)
            else:
                print(f"WARNING: source does not exist: {item}", file=sys.stderr)
                continue
        if dataset == "xview" and source.name in XVIEW_ARCHIVES:
            destination = raw_root / archive_destination_name(source)
            extract_archive(source, destination)
            marker = source.with_suffix(source.suffix + ".extracted")
            if source.parent == raw_root:
                marker.write_text("ok\n", encoding="utf-8")
        else:
            extract_archive(source, raw_root)
    if dataset == "xview":
        normalize_xview_layout(raw_root)
    else:
        extract_nested_archives(raw_root)


def parse_split(value: str) -> tuple[float, float, float]:
    parts = [float(part) for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("split must be train,val,test")
    total = sum(parts)
    if total <= 0:
        raise argparse.ArgumentTypeError("split sum must be > 0")
    return parts[0] / total, parts[1] / total, parts[2] / total


def datasets_from_archive_args(items: list[str]) -> list[str]:
    names: list[str] = []
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --dataset-archive value: {item}")
        name, _source = item.split("=", 1)
        if name not in PROCESSORS:
            raise SystemExit(f"Unsupported dataset in --dataset-archive: {name}")
        if name not in names:
            names.append(name)
    return names


def write_summary(root: Path, stats: list[ConversionStats], registry: ClassRegistry) -> None:
    rows = [
        {
            "dataset": item.dataset,
            "images_seen": item.images_seen,
            "images_written": item.images_written,
            "tiles_written": item.tiles_written,
            "labels_written": item.labels_written,
            "skipped": item.skipped,
        }
        for item in stats
    ]
    (root / "summary.json").write_text(json.dumps({
        "classes": len(registry.names),
        "datasets": rows,
    }, indent=2) + "\n", encoding="utf-8")
    with (root / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["dataset"])
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and prepare GEOINT datasets for YOLOv8 + SAHI.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Dataset workspace root. Defaults to repo/training_dataset.")
    parser.add_argument("--datasets", nargs="+", default=None, choices=list(PROCESSORS), help="Datasets to prepare.")
    parser.add_argument("--download", action="store_true", help="Best-effort download/sync public datasets before conversion.")
    parser.add_argument("--archive", action="append", default=[], help="Archive, directory, or HTTP URL to import into every selected dataset raw folder.")
    parser.add_argument("--dataset-archive", action="append", default=[], help="Per-dataset import in the form dataset=path_or_url.")
    parser.add_argument("--clean", action="store_true", help="Delete existing YOLO output before conversion.")
    parser.add_argument("--split", default="0.8,0.1,0.1", type=parse_split, help="Train,val,test split ratios.")
    parser.add_argument("--tile-size", type=int, default=TILE_SIZE)
    parser.add_argument("--overlap", type=float, default=OVERLAP)
    parser.add_argument("--min-visibility", type=float, default=0.35, help="Minimum original-object area visible in tile.")
    parser.add_argument("--include-empty-ratio", type=float, default=0.0, help="Fraction of empty tiles to keep.")
    parser.add_argument(
        "--max-instances-per-class",
        type=int,
        default=0,
        help="Cap per-class annotation instances within each dataset (0 = no cap). Subsampling is deterministic; "
             "use to balance highly skewed sources such as xView's small_car class.",
    )
    parser.add_argument("--aws-extra", nargs=argparse.REMAINDER, default=[], help="Extra args passed to aws s3 sync after --.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    selected_datasets = args.datasets
    if selected_datasets is None:
        selected_datasets = datasets_from_archive_args(args.dataset_archive) or list(PROCESSORS)
    root = args.root.resolve()
    raw_root = root / "raw"
    yolo_root = root / "yolo"
    ensure_yolo_dirs(yolo_root, clean=args.clean)

    registry = ClassRegistry()
    registry.preload(XVIEW_TYPE_ID_TO_NAME.values())
    registry.preload(DOTA_CLASSES)

    for dataset in selected_datasets:
        dataset_raw = raw_root / dataset
        if args.archive:
            import_archives(dataset, dataset_raw, args.archive)
        for item in args.dataset_archive:
            if "=" not in item:
                raise SystemExit(f"Invalid --dataset-archive value: {item}")
            name, source = item.split("=", 1)
            if name == dataset:
                import_archives(dataset, dataset_raw, [source])
        if args.download:
            download_dataset(dataset, dataset_raw, args)

    stats: list[ConversionStats] = []
    manifest_path = yolo_root / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as manifest:
        for dataset in selected_datasets:
            dataset_raw = raw_root / dataset
            if not dataset_raw.exists():
                print(f"{dataset}: raw directory not found, skipping: {dataset_raw}")
                continue
            stats.append(convert_dataset(
                dataset,
                dataset_raw,
                yolo_root,
                registry,
                args.split,
                args.tile_size,
                args.overlap,
                args.min_visibility,
                args.include_empty_ratio,
                args.max_instances_per_class,
                manifest,
            ))

    write_data_yaml(yolo_root, registry)
    write_summary(yolo_root, stats, registry)
    print("\nPrepared YOLO dataset:")
    print(f"  {yolo_root}")
    print(f"  classes: {len(registry.names)}")
    for item in stats:
        print(f"  {item.dataset}: {item.tiles_written} tiles, {item.labels_written} labels")
    print("\nTrain with:")
    print(f"  python inference/train_model.py --data {yolo_root / 'data.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
