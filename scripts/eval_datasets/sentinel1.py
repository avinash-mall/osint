"""Sentinel-1 GRD dataset loader skeleton (Phase 9.43).

Sentinel-1 IW GRD is the publicly available SAR slice that Sentinel needs
to validate the Phase 5 work — SAR-specific NMS thresholds, the
SAM3-on-SAR opt-in toggle, and (eventually) the CFAR point-target
detector. Ships move, and SAR sees through cloud + night, so an honest
ship-recall measurement on Sentinel-1 is the regression gate for any
maritime change.

Source: <https://browser.dataspace.copernicus.eu/> (free Copernicus account
or AWS Open Data ``sentinel-s1-l1c`` bucket).

Layout::

    inference-sam3/eval/datasets/sentinel1/
        labels.json
        chips/
            chip_0000.tif    # 2-band float32 (VV, VH) in dB, calibrated
            ...

Each ``labels.json`` entry::

    {
        "chip_file": "chips/chip_0017.tif",
        "modality": "sar",
        "source": "sentinel1:S1A_IW_GRDH_xyz",
        "sar_metadata": {
            "incidence_angle_deg": 38.4,
            "look_direction": "RIGHT",
            "orbit_direction": "DESCENDING",
            "polarizations": ["VV", "VH"]
        },
        "ground_truth": {"is_real": true},
        "annotations": [
            {"label": "ship", "bbox_xyxy": [x1,y1,x2,y2]}
        ]
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATASET_DIR = _REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "sentinel1"


# Sentinel-1 GRD is dominated by ship detection in the public datasets.
# We keep the class list small because SAR's domain on military analysis
# is overwhelmingly maritime activity + larger ground vehicles.
SENTINEL1_CLASSES: list[str] = [
    "ship",
    "fishing-vessel",
    "cargo-ship",
    "oil-tanker",
    "container-ship",
    "warship",
    "submarine-snorkel",
    "harbor",
    "large-vehicle",
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
            "modality": record.get("modality", "sar"),
            "prompts": [a.get("label") for a in record.get("annotations", []) if a.get("label")],
            "ground_truth": record.get("annotations", []),
            "source": record.get("source", "sentinel1"),
            # Surface SAR-specific metadata so downstream eval can apply the
            # incidence-angle layover threshold logic.
            "sar_metadata": record.get("sar_metadata") or {},
        }


def iter_sentinel1(dataset_dir: Path = _DEFAULT_DATASET_DIR) -> Iterator[tuple]:
    for sample in iter_samples(dataset_dir):
        with open(sample["chip_path"], "rb") as f:
            chip_bytes = f.read()
        yield chip_bytes, sample["modality"], sample["prompts"], sample["ground_truth"]
