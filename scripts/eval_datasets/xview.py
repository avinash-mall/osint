"""xView dataset loader skeleton (Phase 9.43).

xView is the largest publicly available military-relevant aerial dataset
(60 classes, 1 million labelled objects, 0.3 m GSD over ~1,400 km²).
The classes include several Sentinel can't currently detect well —
``Aircraft Hangar``, ``Storage Tank``, ``Construction Site``,
``Excavator``, ``Crane``, ``Helipad`` — which makes it the highest-value
external slice for measuring the per-class recall lift from Phase 1.

Source: <https://challenge.xviewdataset.org/> (registration required).

Layout expected (matching ``scripts/fetch_real_datasets`` convention)::

    inference-sam3/eval/datasets/xview/
        labels.json          # records produced by an external fetcher
        chips/
            chip_0000.png
            chip_0001.png
            ...

Each ``labels.json`` entry::

    {
        "chip_file": "chips/chip_0123.png",
        "modality": "rgb",
        "source": "xview:train:abc123",
        "ground_truth": {"is_real": true},
        "annotations": [
            {"label": "Fixed-wing Aircraft", "bbox_xyxy": [x1,y1,x2,y2]},
            ...
        ]
    }

This module ships as a skeleton because actually fetching xView requires
a signed challenge agreement; we expose ``iter_samples`` so the inference
harness can consume the slice once an operator drops it in place. The
``XVIEW_CLASSES`` constant lists the published xView label set so the
ontology matchers in ``backend/scripts/seeds/defenceOntology.seed.json``
can be extended to bridge those labels to the right branch.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATASET_DIR = _REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "xview"


# Subset of xView labels most relevant to defence-focused recall measurement.
# Full list is 60 classes; this subset is the one the Sentinel ontology
# currently has matchers for (or should have, post-Phase 1).
XVIEW_CLASSES: list[str] = [
    "Fixed-wing Aircraft",
    "Small Aircraft",
    "Cargo Plane",
    "Helicopter",
    "Passenger Vehicle",
    "Small Car",
    "Bus",
    "Pickup Truck",
    "Utility Truck",
    "Truck",
    "Cargo Truck",
    "Truck w/Box",
    "Truck Tractor",
    "Trailer",
    "Truck w/Flatbed",
    "Truck w/Liquid",
    "Crane Truck",
    "Railway Vehicle",
    "Passenger Car",
    "Cargo Car",
    "Flat Car",
    "Tank car",
    "Locomotive",
    "Maritime Vessel",
    "Motorboat",
    "Sailboat",
    "Tugboat",
    "Barge",
    "Fishing Vessel",
    "Ferry",
    "Yacht",
    "Container Ship",
    "Oil Tanker",
    "Engineering Vehicle",
    "Tower crane",
    "Container Crane",
    "Reach Stacker",
    "Straddle Carrier",
    "Mobile Crane",
    "Dump Truck",
    "Haul Truck",
    "Scraper/Tractor",
    "Front loader/Bulldozer",
    "Excavator",
    "Cement Mixer",
    "Ground Grader",
    "Hut/Tent",
    "Shed",
    "Building",
    "Aircraft Hangar",
    "Damaged Building",
    "Facility",
    "Construction Site",
    "Vehicle Lot",
    "Helipad",
    "Storage Tank",
    "Shipping container lot",
    "Shipping Container",
    "Pylon",
    "Tower",
]


def iter_samples(dataset_dir: Path = _DEFAULT_DATASET_DIR) -> Iterator[dict]:
    """Yield record dicts from ``labels.json``. Returns nothing when the
    slice hasn't been populated, so the harness can skip xView gracefully
    in environments where the dataset agreement hasn't been signed.
    """
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
            "source": record.get("source", "xview"),
        }


def iter_xview(dataset_dir: Path = _DEFAULT_DATASET_DIR) -> Iterator[tuple]:
    """Tuple-based API matching ``iter_dota`` for the comparison harness."""
    for sample in iter_samples(dataset_dir):
        with open(sample["chip_path"], "rb") as f:
            chip_bytes = f.read()
        yield chip_bytes, sample["modality"], sample["prompts"], sample["ground_truth"]
