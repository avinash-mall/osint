"""FAIR1M dataset loader skeleton (Phase 9.43).

FAIR1M is a fine-grained aerial object dataset with 37 classes covering
military-relevant ship and aircraft sub-types (Boeing 737/747/777,
A330/A350, Liaoning aircraft carrier, Arleigh-Burke destroyer, etc.).
At 0.3–0.8 m GSD it's the closest publicly available proxy for the
WorldView / Maxar imagery a defence analyst actually works with, and
its fine-grained labels are the right yardstick for Phase 1.1's expanded
SAM3 prompt curation.

Source: <https://www.gaofen-challenge.com/benchmark> (registration required).

Skeleton mirrors ``scripts/eval_datasets/xview.py`` — populate the
``inference-sam3/eval/datasets/fair1m/labels.json`` and ``chips/`` from
an external fetcher; this loader consumes them via the same
``iter_samples`` / ``iter_fair1m`` API.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATASET_DIR = _REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "fair1m"


FAIR1M_CLASSES: list[str] = [
    # Aircraft (fine-grained Boeing/Airbus + military)
    "Boeing737", "Boeing747", "Boeing777", "Boeing787",
    "A220", "A321", "A330", "A350",
    "C919", "ARJ21",
    "other-airplane", "Passenger Ship",
    # Military aircraft
    "Liaoning",          # PLA-N aircraft carrier
    "fighter", "trainer", "warship",
    # Ships
    "Motorboat", "Fishing Boat", "Tugboat", "Engineering Ship",
    "Liquid Cargo Ship", "Dry Cargo Ship", "Warship",
    "other-ship",
    # Vehicles
    "small-car", "bus", "cargo-truck", "dump-truck",
    "van", "trailer", "tractor", "truck-tractor",
    "other-vehicle",
    # Sport courts (DOTA-compat)
    "Basketball Court", "Tennis Court", "Football Field",
    "Baseball Field",
    # Structures
    "Bridge", "Roundabout",
    "Intersection", "Boeing-727",
]


def iter_samples(dataset_dir: Path = _DEFAULT_DATASET_DIR) -> Iterator[dict]:
    labels_path = dataset_dir / "labels.json"
    if not labels_path.exists():
        return
    records = json.loads(labels_path.read_text(encoding="utf-8"))
    for record in records:
        chip_path = dataset_dir / record["chip_file"]
        if not chip_path.exists():
            continue
        yield {
            "chip_path": chip_path,
            "modality": record.get("modality", "rgb"),
            "prompts": [a.get("label") for a in record.get("annotations", []) if a.get("label")],
            "ground_truth": record.get("annotations", []),
            "source": record.get("source", "fair1m"),
        }


def iter_fair1m(dataset_dir: Path = _DEFAULT_DATASET_DIR) -> Iterator[tuple]:
    for sample in iter_samples(dataset_dir):
        with open(sample["chip_path"], "rb") as f:
            chip_bytes = f.read()
        yield chip_bytes, sample["modality"], sample["prompts"], sample["ground_truth"]
