"""
sen1floods.py
=============
Dataset loader for the Sen1Floods11 flood detection dataset.

Real dataset
------------
Sen1Floods11 provides SAR (Sentinel-1) and multispectral (Sentinel-2) imagery
paired with flood binary masks.  Available at:
https://github.com/cloudtostreet/Sen1Floods11

For the PRITHVI flood head we use the multispectral modality.

Synthetic fallback (always used in tests)
-----------------------------------------
This module generates 10 synthetic 6-channel uint16 chips (64×64) as
multi-band GeoTIFFs (via rasterio) or NPZ files (rasterio unavailable).
Half have ``gt = {"flood": True}``, half ``False``.

Chips are saved to::

    inference-sam3/eval/datasets/sen1floods/chips/

Generation is idempotent: skipped if ``labels.json`` already exists with ≥ 5
entries.

Usage
-----
::

    from eval_datasets.sen1floods import iter_sen1floods

    for chip_bytes, modality, prompts, ground_truth in iter_sen1floods(max_chips=5):
        print(modality, ground_truth)

Tuple format
------------
``(chip_bytes: bytes, modality: str, prompts: list[str], ground_truth: dict)``

- ``modality``     = ``"multispectral"`` (PRITHVI flood head; SAR also available)
- ``prompts``      = ``[]`` (PRITHVI doesn't use text prompts)
- ``ground_truth`` = ``{"flood": bool}``
- ``chip_bytes``   = raw bytes of a 6-channel uint16 GeoTIFF (or NPZ fallback)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Generator

import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATASET_DIR = (
    _REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "sen1floods"
)
_CHIPS_DIR_NAME = "chips"
_LABELS_FNAME = "labels.json"

# ---------------------------------------------------------------------------
# Synthetic generation constants
# ---------------------------------------------------------------------------

_N_CHIPS = 10
_CHIP_H = 64
_CHIP_W = 64
_N_BANDS = 6


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _write_tiff(path: Path, data: np.ndarray) -> None:
    """Write a multi-band uint16 GeoTIFF using rasterio.

    Parameters
    ----------
    path:
        Destination ``.tif`` file path.
    data:
        Array of shape ``(bands, H, W)``, dtype ``uint16``.
    """
    import rasterio  # noqa: PLC0415
    from rasterio.transform import from_bounds  # noqa: PLC0415

    bands, height, width = data.shape
    transform = from_bounds(0, 0, 1, 1, width, height)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=bands,
        dtype=rasterio.uint16,
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        for b in range(bands):
            dst.write(data[b], b + 1)


def _write_npz(path: Path, data: np.ndarray) -> None:
    """Write array as NPZ (fallback when rasterio is not installed).

    The real pipeline uses rasterio GeoTIFFs; NPZ is used only for testing.
    """
    np.savez_compressed(str(path), data=data)


def _read_chip_bytes(chip_path: Path) -> bytes:
    """Return raw bytes from a chip file (TIFF or NPZ)."""
    return chip_path.read_bytes()


# ---------------------------------------------------------------------------
# Synthetic dataset generation
# ---------------------------------------------------------------------------

def _generate_synthetic_sen1floods(output_dir: Path) -> None:
    """Generate synthetic Sen1Floods11 chips and a ``labels.json`` manifest.

    Creates ``_N_CHIPS`` synthetic 6-channel uint16 chips of size
    ``(_CHIP_H, _CHIP_W)``.  Half are labeled ``flood=True``.

    Parameters
    ----------
    output_dir:
        Parent directory (``sen1floods/``).  ``chips/`` subdirectory is created.
    """
    chips_dir = output_dir / _CHIPS_DIR_NAME
    chips_dir.mkdir(parents=True, exist_ok=True)

    np_rng = np.random.default_rng(1)

    records: list[dict] = []

    # Try rasterio; fall back to NPZ
    try:
        import rasterio  # noqa: F401
        use_tiff = True
        ext = ".tif"
        log.debug("rasterio available — writing GeoTIFFs")
    except ImportError:
        use_tiff = False
        ext = ".npz"
        log.debug("rasterio not available — writing NPZ fallback chips")

    for i in range(_N_CHIPS):
        # Alternate positive/negative
        gt_positive = i % 2 == 0

        # Synthetic 6-band uint16 array (values 0–4095, scaled to uint16)
        data = np_rng.integers(0, 4096, size=(_N_BANDS, _CHIP_H, _CHIP_W), dtype=np.uint16)

        chip_filename = f"chip_{i:04d}{ext}"
        chip_path = chips_dir / chip_filename

        if use_tiff:
            _write_tiff(chip_path, data)
        else:
            _write_npz(chip_path, data)

        records.append({
            "chip_file": f"{_CHIPS_DIR_NAME}/{chip_filename}",
            "modality": "multispectral",
            "flood": gt_positive,
        })

    labels_path = output_dir / _LABELS_FNAME
    labels_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    log.info("Generated %d synthetic Sen1Floods11 chips in %s", len(records), output_dir)


def _ensure_dataset(dataset_dir: Path) -> None:
    """Generate synthetic chips if the dataset is missing or incomplete."""
    labels_path = dataset_dir / _LABELS_FNAME
    if labels_path.exists():
        try:
            records = json.loads(labels_path.read_text(encoding="utf-8"))
            if len(records) >= 5:
                return  # dataset already populated
        except (json.JSONDecodeError, TypeError):
            pass  # corrupt file — regenerate

    _generate_synthetic_sen1floods(dataset_dir)


# ---------------------------------------------------------------------------
# Public iterator
# ---------------------------------------------------------------------------

def iter_sen1floods(
    labels_path: str | None = None,
    max_chips: int | None = None,
) -> Generator[tuple[bytes, str, list[str], dict], None, None]:
    """Yield (chip_bytes, modality, prompts, ground_truth) tuples.

    Parameters
    ----------
    labels_path:
        Path to a ``labels.json`` manifest.  Defaults to the synthetic
        dataset under ``inference-sam3/eval/datasets/sen1floods/``.
    max_chips:
        If set, stop after yielding this many chips.

    Yields
    ------
    chip_bytes : bytes
        Raw bytes of a 6-channel uint16 GeoTIFF (or NPZ fallback).
    modality : str
        Always ``"multispectral"`` — PRITHVI flood head uses multispectral.
        (SAR is also available in the real dataset but not used here.)
    prompts : list[str]
        Always ``[]`` — PRITHVI does not use text prompts.
    ground_truth : dict
        ``{"flood": bool}`` — True if the chip contains flood water.
    """
    if labels_path is None:
        dataset_dir = _DEFAULT_DATASET_DIR
        resolved = dataset_dir / _LABELS_FNAME
    else:
        resolved = Path(labels_path).resolve()
        dataset_dir = resolved.parent

    if not resolved.exists():
        log.warning("Sen1Floods11 labels.json not found at %s — yielding nothing", resolved)
        return

    records: list[dict] = json.loads(resolved.read_text(encoding="utf-8"))
    base_dir = dataset_dir

    count = 0
    for record in records:
        if max_chips is not None and count >= max_chips:
            break

        chip_rel: str = record["chip_file"]
        chip_path: Path = (base_dir / chip_rel).resolve()

        # Safety: prevent path traversal
        if not str(chip_path).startswith(str(base_dir.resolve())):
            log.warning("Path traversal detected — skipping %s", chip_rel)
            continue

        if not chip_path.exists():
            log.warning("Chip file missing — skipping %s", chip_path)
            continue

        chip_bytes: bytes = _read_chip_bytes(chip_path)
        gt_positive: bool = bool(record.get("flood", False))

        yield chip_bytes, "multispectral", [], {"flood": gt_positive}
        count += 1
