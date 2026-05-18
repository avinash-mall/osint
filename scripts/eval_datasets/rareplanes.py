"""RarePlanes dataset loader skeleton (Phase 9.43).

RarePlanes is the AI2 / CosmiQ Works aerial dataset purpose-built for
fine-grained aircraft detection (~16,000 real chips + 35,000 synthetic
WorldView-3-class chips at 0.3 m GSD). Its 110 aircraft sub-classes
(F-16, F-15, MQ-9, A-10, KC-135, …) are the gold standard for measuring
Phase 1's military-aircraft recall lift.

Source: <https://www.cosmiqworks.org/rareplanes/> (registration required).

Same skeleton API as ``xview.py`` / ``fair1m.py`` — populate
``inference-sam3/eval/datasets/rareplanes/labels.json`` + ``chips/``
externally, then call ``iter_samples`` / ``iter_rareplanes`` from the
comparison harness.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATASET_DIR = _REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "rareplanes"


# Coarse aircraft categories used by the RarePlanes ``role`` annotation.
# The full 110-class taxonomy is documented at the dataset URL and includes
# tail-number-specific labels we don't expose here.
RAREPLANES_ROLES: list[str] = [
    "Small Civil Transport/Utility",
    "Medium Civil Transport/Utility",
    "Large Civil Transport/Utility",
    "Military Transport/Utility/AWAC",
    "Military Bomber",
    "Military Fighter/Interceptor/Attack",
    "Military Trainer",
    "Military Cargo",
    "Military Patrol",
    "Military Recon/Surveillance",
    "Helicopter",
    "Unmanned Aerial Vehicle",
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
            "source": record.get("source", "rareplanes"),
        }


def iter_rareplanes(dataset_dir: Path = _DEFAULT_DATASET_DIR) -> Iterator[tuple]:
    for sample in iter_samples(dataset_dir):
        with open(sample["chip_path"], "rb") as f:
            chip_bytes = f.read()
        yield chip_bytes, sample["modality"], sample["prompts"], sample["ground_truth"]
