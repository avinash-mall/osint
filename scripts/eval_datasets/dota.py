"""Dataset loader for the checked-in DOTA-v1.0 validation slice.

Synthetic fixtures are still available to tests through
``scripts.fetch_eval_datasets.generate_synthetic_dota()``, but production
iteration never manufactures data when a dataset is missing.

Usage
-----
::

    from pathlib import Path
    from scripts.eval_datasets.dota import iter_dota, iter_samples, DOTA_CLASSES

    # New tuple-based API (preferred for comparison harness):
    for chip_bytes, modality, prompts, ground_truth in iter_dota():
        print(modality, prompts)

    # Legacy dict-based API:
    dataset_dir = Path("inference-sam3/eval/datasets/dota_val")
    for sample in iter_samples(dataset_dir):
        print(sample["chip_path"], sample["modality"], sample["ground_truth"])

``iter_dota`` yields ``(chip_bytes, modality, prompts, ground_truth)`` tuples
where ground_truth items have the shape::

    {"label": str, "bbox_xyxy": [x1, y1, x2, y2]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Generator, Iterator

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

DOTA_CLASSES: list[str] = [
    "plane",
    "ship",
    "storage-tank",
    "baseball-diamond",
    "tennis-court",
    "basketball-court",
    "ground-track-field",
    "harbor",
    "bridge",
    "large-vehicle",
    "small-vehicle",
    "helicopter",
    "roundabout",
    "soccer-ball-field",
    "swimming-pool",
    "container-crane",
    "airport",
    "helipad",
]

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATASET_DIR = _REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "dota_val"
_DEFAULT_LABELS_PATH = _REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "dota" / "labels.json"

# ---------------------------------------------------------------------------
# Public API — tuple-based (comparison harness interface)
# ---------------------------------------------------------------------------

def iter_dota(
    labels_path: str | None = None,
    max_chips: int | None = None,
) -> Generator[tuple[bytes, str, list[str], list[dict]], None, None]:
    """Yield (chip_bytes, modality, prompts, ground_truth) tuples from a DOTA labels.json.

    Parameters
    ----------
    labels_path:
        Path to the labels.json produced by ``scripts/fetch_eval_datasets.py``.
        Defaults to ``inference-sam3/eval/datasets/dota/labels.json``.
    max_chips:
        If set, stop after yielding this many chips.

    Yields
    ------
    chip_bytes : bytes
        Raw PNG bytes for the chip.
    modality : str
        Always ``"rgb"`` for DOTA.
    prompts : list[str]
        Unique DOTA class names present in this chip's annotations (for SAM3
        text-prompt conditioning).
    ground_truth : list[dict]
        List of ``{"label": str, "bbox_xyxy": [x1, y1, x2, y2]}`` dicts.

    Notes
    -----
    Chips whose files do not exist on disk are silently skipped so a partial
    dataset still works.  The ``chip_file`` paths in labels.json are resolved
    relative to the directory that contains labels.json.
    """
    if labels_path is None:
        resolved = _DEFAULT_LABELS_PATH
    else:
        resolved = Path(labels_path).resolve()

    if not resolved.exists():
        return  # nothing to iterate

    base_dir = resolved.parent

    with resolved.open() as fh:
        records: list[dict] = json.load(fh)

    count = 0
    for record in records:
        if max_chips is not None and count >= max_chips:
            break

        chip_rel: str = record["chip_file"]
        chip_path: Path = (base_dir / chip_rel).resolve()

        # Prevent path traversal attacks
        if not str(chip_path).startswith(str(base_dir.resolve())):
            continue  # Skip path traversal attempts

        if not chip_path.exists():
            # Skip missing chips rather than crashing the whole iteration
            continue

        chip_bytes: bytes = chip_path.read_bytes()

        raw_annotations: list[dict] = record.get("annotations", [])
        ground_truth: list[dict] = [
            {
                "label": ann["label"],
                "bbox_xyxy": ann["bbox_xyxy"],
            }
            for ann in raw_annotations
        ]

        # Prompts = unique DOTA class names present in this chip's GT
        prompts: list[str] = sorted({ann["label"] for ann in ground_truth})
        modality: str = record.get("modality", "rgb")

        yield chip_bytes, modality, prompts, ground_truth
        count += 1


# ---------------------------------------------------------------------------
# Public API — legacy dict-based (kept for backward compatibility)
# ---------------------------------------------------------------------------

def iter_samples(dataset_dir: Path | None = None) -> Iterator[dict]:
    """
    Iterate over samples from a DOTA-v1.0 validation slice.

    Parameters
    ----------
    dataset_dir:
        Directory containing ``chip_*.png`` files and ``labels.json``.
        Defaults to ``inference-sam3/eval/datasets/dota_val/``.

    Yields
    ------
    dict
        Keys: ``chip_path`` (Path), ``chip_bytes`` (bytes), ``modality`` (str),
        ``prompts`` (list[str]), ``ground_truth`` (list[dict]).
    """
    if dataset_dir is None:
        dataset_dir = _DEFAULT_DATASET_DIR
    dataset_dir = Path(dataset_dir).resolve()

    labels_path = dataset_dir / "labels.json"
    if not labels_path.exists():
        return  # nothing to iterate

    with labels_path.open() as fh:
        records: list[dict] = json.load(fh)

    for record in records:
        chip_name: str = record["chip"]
        chip_path: Path = dataset_dir / chip_name

        if not chip_path.exists():
            # Skip missing chips rather than crashing the whole iteration
            continue

        chip_bytes: bytes = chip_path.read_bytes()

        raw_boxes: list[dict] = record.get("boxes", [])
        ground_truth: list[dict] = [
            {
                "label": box["label"],
                "bbox_xyxy": box["bbox_xyxy"],
            }
            for box in raw_boxes
        ]

        # Prompts = unique DOTA class names present in this chip's GT
        prompts: list[str] = sorted({box["label"] for box in ground_truth})

        yield {
            "chip_path": chip_path,
            "chip_bytes": chip_bytes,
            "modality": "rgb",
            "prompts": prompts,
            "ground_truth": ground_truth,
        }
