#!/usr/bin/env python3
"""
Prepare overhead-imagery datasets for SAHI + YOLOv8 training.

Default output is the repository-level training_dataset/ directory:

  training_dataset/
    raw/{xview,dota,fmow,rareplanes,fair1m}/
    yolo/{train,val,test}/{images,labels}/
    yolo/data.yaml
    yolo/classes.json
    yolo/manifest.jsonl

The script converts available raw datasets into YOLO horizontal bounding boxes.
DOTA and FAIR1M oriented boxes are converted to enclosing horizontal boxes because
the current inference service uses standard YOLOv8 + SAHI detection, not YOLO-OBB.

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


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "training_dataset"
DEFAULT_YOLO_ROOT = DEFAULT_ROOT / "yolo"
DEFAULT_RAW_ROOT = DEFAULT_ROOT / "raw"
TILE_SIZE = 640
OVERLAP = 0.2
DEFAULT_SPLIT = (0.8, 0.1, 0.1)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


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

FMOW_CLASSES = [
    "fmow_airport",
    "fmow_airport_hangar",
    "fmow_airport_terminal",
    "fmow_amusement_park",
    "fmow_aquaculture",
    "fmow_archaeological_site",
    "fmow_barn",
    "fmow_border_checkpoint",
    "fmow_burial_site",
    "fmow_car_dealership",
    "fmow_construction_site",
    "fmow_crop_field",
    "fmow_dam",
    "fmow_debris_or_rubble",
    "fmow_educational_institution",
    "fmow_electric_substation",
    "fmow_factory_or_powerplant",
    "fmow_fire_station",
    "fmow_flooded_road",
    "fmow_fountain",
    "fmow_gas_station",
    "fmow_golf_course",
    "fmow_ground_transportation_station",
    "fmow_helipad",
    "fmow_hospital",
    "fmow_impoverished_settlement",
    "fmow_interchange",
    "fmow_lake_or_pond",
    "fmow_lighthouse",
    "fmow_military_facility",
    "fmow_multi_unit_residential",
    "fmow_nuclear_powerplant",
    "fmow_office_building",
    "fmow_oil_or_gas_facility",
    "fmow_park",
    "fmow_parking_lot_or_garage",
    "fmow_place_of_worship",
    "fmow_police_station",
    "fmow_port",
    "fmow_prison",
    "fmow_race_track",
    "fmow_railway_bridge",
    "fmow_recreational_facility",
    "fmow_road_bridge",
    "fmow_runway",
    "fmow_shipyard",
    "fmow_shopping_mall",
    "fmow_single_unit_residential",
    "fmow_smokestack",
    "fmow_solar_farm",
    "fmow_space_facility",
    "fmow_stadium",
    "fmow_storage_tank",
    "fmow_surface_mine",
    "fmow_swimming_pool",
    "fmow_toll_booth",
    "fmow_tower",
    "fmow_tunnel_opening",
    "fmow_waste_disposal",
    "fmow_water_treatment_facility",
    "fmow_wind_farm",
    "fmow_zoo",
]


@dataclass(frozen=True)
class Annotation:
    label: str
    bbox: tuple[float, float, float, float]
    source: str


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


def polygon_to_bbox(values: list[float]) -> tuple[float, float, float, float] | None:
    if len(values) < 4:
        return None
    if len(values) == 4:
        x1, y1, x2, y2 = values
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
    xs = values[0::2]
    ys = values[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def yolo_line(class_id: int, bbox: tuple[float, float, float, float], tile_size: int) -> str | None:
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return None
    cx = ((x1 + x2) / 2.0) / tile_size
    cy = ((y1 + y2) / 2.0) / tile_size
    width = (x2 - x1) / tile_size
    height = (y2 - y1) / tile_size
    if width <= 0 or height <= 0:
        return None
    return f"{class_id} {cx:.6f} {cy:.6f} {width:.6f} {height:.6f}"


def find_images(root: Path) -> dict[str, Path]:
    images: dict[str, Path] = {}
    if not root.exists():
        return images
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            images.setdefault(path.name, path)
            images.setdefault(path.stem, path)
    return images


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
        Annotation(ann.label, clamped, ann.source)
        for ann in annotations
        if (clamped := clamp_bbox(ann.bbox, width, height)) is not None
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
                local = (
                    intersection[0] - x0,
                    intersection[1] - y0,
                    intersection[2] - x0,
                    intersection[3] - y0,
                )
                line = yolo_line(registry.add(ann.label), local, tile_size)
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
    geojson_candidates = list(raw_root.rglob("*train*.geojson")) + list(raw_root.rglob("*.geojson"))
    if not geojson_candidates:
        print("xView: no GeoJSON labels found")
        return {}
    geojson_path = geojson_candidates[0]
    images = find_images(raw_root)
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    grouped: dict[Path, list[Annotation]] = {}
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        image_id = str(props.get("image_id") or props.get("IMAGE_ID") or "")
        image_path = images.get(image_id) or images.get(Path(image_id).stem)
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
        bbox = None
        for key in ("bounds_imcoords", "BOUNDS_IMCOORDS"):
            if props.get(key):
                bbox = polygon_to_bbox(parse_number_list(props[key]))
                break
        if bbox is None:
            coords = feature.get("geometry", {}).get("coordinates", [])
            bbox = polygon_to_bbox(parse_number_list(coords))
        if bbox:
            grouped.setdefault(image_path, []).append(Annotation(label, bbox, "xview"))
    return grouped


def process_dota(raw_root: Path) -> dict[Path, list[Annotation]]:
    images = find_images(raw_root)
    label_files = list(raw_root.rglob("labelTxt/*.txt")) or list(raw_root.rglob("*.txt"))
    grouped: dict[Path, list[Annotation]] = {}
    for label_path in label_files:
        image_path = images.get(label_path.stem)
        if not image_path:
            continue
        annotations: list[Annotation] = []
        for line in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.strip().split()
            if len(parts) < 9 or parts[0].lower().startswith("imagesource"):
                continue
            try:
                coords = [float(part) for part in parts[:8]]
            except ValueError:
                continue
            label = f"dota_{sanitize_label(parts[8])}"
            bbox = polygon_to_bbox(coords)
            if bbox:
                annotations.append(Annotation(label, bbox, "dota"))
        if annotations:
            grouped[image_path] = annotations
    return grouped


def process_fair1m(raw_root: Path) -> dict[Path, list[Annotation]]:
    images = find_images(raw_root)
    grouped: dict[Path, list[Annotation]] = {}
    for xml_path in raw_root.rglob("*.xml"):
        image_path = images.get(xml_path.stem)
        if not image_path:
            continue
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue
        annotations: list[Annotation] = []
        for obj in root.findall(".//object"):
            name_node = obj.find(".//possibleresult/name") or obj.find("name")
            label = f"fair1m_{sanitize_label(name_node.text if name_node is not None else 'unknown')}"
            points = []
            for point in obj.findall(".//point"):
                points.extend(parse_number_list(point.text or ""))
            if not points:
                for tag in ("bndbox", "robndbox"):
                    node = obj.find(f".//{tag}")
                    if node is not None:
                        points.extend(parse_number_list([child.text for child in node if child.text]))
            bbox = polygon_to_bbox(points)
            if bbox:
                annotations.append(Annotation(label, bbox, "fair1m"))
        if annotations:
            grouped[image_path] = annotations
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
            grouped.setdefault(image_path, []).append(Annotation(label, (x, y, x + w, y + h), dataset_prefix))
        if grouped:
            break
    return grouped


def process_fmow(raw_root: Path) -> dict[Path, list[Annotation]]:
    images = find_images(raw_root)
    grouped: dict[Path, list[Annotation]] = {}
    for json_path in raw_root.rglob("*.json"):
        try:
            metadata = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        candidates = [
            images.get(json_path.with_suffix(ext).name)
            for ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff")
        ]
        image_path = next((candidate for candidate in candidates if candidate), None)
        if not image_path:
            continue
        category = metadata.get("category") or json_path.parent.name
        label = f"fmow_{sanitize_label(category)}"
        annotations: list[Annotation] = []
        boxes = metadata.get("bounding_boxes") or metadata.get("boxes") or []
        for item in boxes:
            raw_box = item.get("box") if isinstance(item, dict) else item
            values = parse_number_list(raw_box)
            bbox = polygon_to_bbox(values)
            if bbox:
                annotations.append(Annotation(label, bbox, "fmow"))
        if not annotations:
            try:
                with Image.open(image_path) as image:
                    width, height = image.size
                annotations.append(Annotation(label, (0.0, 0.0, float(width), float(height)), "fmow"))
            except Exception:
                continue
        grouped[image_path] = annotations
    return grouped


PROCESSORS = {
    "xview": process_xview,
    "dota": process_dota,
    "fmow": process_fmow,
    "rareplanes": lambda root: process_coco(root, "rareplanes"),
    "fair1m": process_fair1m,
}


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
    manifest,
) -> ConversionStats:
    processor = PROCESSORS[dataset]
    grouped = processor(raw_root)
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
    elif archive.suffix.lower() in {".tar", ".gz", ".tgz", ".bz2", ".xz"} or archive.name.endswith((".tar.gz", ".tar.bz2")):
        with tarfile.open(archive, "r:*") as tf:
            tf.extractall(destination)


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
        print("Expected files include xView_train.geojson and train/validation image archives.")
        return
    if dataset == "dota":
        print("DOTA downloads are distributed from the official DOTA site and mirrors. Place extracted images and labelTxt under:")
        print(f"  {raw_root}")
        return
    if dataset == "fmow":
        bucket = "s3://spacenet-dataset/Hosted-Datasets/fmow/fmow-rgb"
        print(f"Syncing fMoW RGB from {bucket}. This is about 200 GB.")
        run_aws_sync(bucket, raw_root, args.aws_extra)
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
        extract_archive(source, raw_root)


def parse_split(value: str) -> tuple[float, float, float]:
    parts = [float(part) for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("split must be train,val,test")
    total = sum(parts)
    if total <= 0:
        raise argparse.ArgumentTypeError("split sum must be > 0")
    return parts[0] / total, parts[1] / total, parts[2] / total


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
    parser.add_argument("--datasets", nargs="+", default=list(PROCESSORS), choices=list(PROCESSORS), help="Datasets to prepare.")
    parser.add_argument("--download", action="store_true", help="Best-effort download/sync public datasets before conversion.")
    parser.add_argument("--archive", action="append", default=[], help="Archive, directory, or HTTP URL to import into every selected dataset raw folder.")
    parser.add_argument("--dataset-archive", action="append", default=[], help="Per-dataset import in the form dataset=path_or_url.")
    parser.add_argument("--clean", action="store_true", help="Delete existing YOLO output before conversion.")
    parser.add_argument("--split", default="0.8,0.1,0.1", type=parse_split, help="Train,val,test split ratios.")
    parser.add_argument("--tile-size", type=int, default=TILE_SIZE)
    parser.add_argument("--overlap", type=float, default=OVERLAP)
    parser.add_argument("--min-visibility", type=float, default=0.35, help="Minimum original-object area visible in tile.")
    parser.add_argument("--include-empty-ratio", type=float, default=0.0, help="Fraction of empty tiles to keep.")
    parser.add_argument("--aws-extra", nargs=argparse.REMAINDER, default=[], help="Extra args passed to aws s3 sync after --.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    raw_root = root / "raw"
    yolo_root = root / "yolo"
    ensure_yolo_dirs(yolo_root, clean=args.clean)

    registry = ClassRegistry()
    registry.preload(XVIEW_TYPE_ID_TO_NAME.values())
    registry.preload(DOTA_CLASSES)
    registry.preload(FMOW_CLASSES)

    for dataset in args.datasets:
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
        for dataset in args.datasets:
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
