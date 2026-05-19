"""
sar_synth.py
============
Synthetic Sentinel-1-format dataset loader for the TERRAMIND latency test.

Real Sentinel-1 GRD VV/VH GeoTIFFs are not freely available on HuggingFace at
a manageable size (the SSL4EO-S12 S1 archive is 480 GB; raw GRD is gigabytes
per scene from ESA Copernicus). For the comparison harness we instead
generate SAR-formatted 2-band uint16 GeoTIFFs whose values are drawn from
realistic Sentinel-1 dB ranges:

  - VV polarization: [-25, +5] dB (water ≈ -22, land ≈ -10)
  - VH polarization: [-30, -5] dB (cross-pol weaker than co-pol)

The synthetic backscatter has structure (gradients + noise spots) so the
TERRAMIND patch encoder produces a non-trivial embedding rather than degenerating.

This is **latency-only** — quality measurement requires real S1 GRD with
real ground-truth annotations (e.g. an actual ship-detection benchmark in
GRD format). See docs/inference_layer_comparison.md for the explicit caveat.

Tuple format
------------
``(chip_bytes: bytes, modality: str, prompts: list[str], ground_truth: dict)``

- modality   = "sar"
- prompts    = ["ship", "vessel", "boat"] (so SAM3 has something to detect
               in the SAR-to-RGB preview)
- ground_truth = {} (no real GT — quality numbers will be 0 for this slice)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Generator

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DATASET_DIR = REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "sar_synth"
_LABELS_FNAME = "labels.json"
_N_SYNTHETIC = 10
_CHIP_HW = (256, 256)

log = logging.getLogger("eval_datasets.sar_synth")


def _generate_chip(rng: np.random.Generator) -> np.ndarray:
    """Generate a single 2-band SAR chip in raw amplitude (the SAR decoder
    will log10 + dB-clip + normalize).

    Returns shape (2, H, W) float32 — VV in [0], VH in [1].
    """
    h, w = _CHIP_HW

    # Background backscatter (water-like, low dB)
    base_vv_db = rng.uniform(-22.0, -18.0, size=(h, w)).astype(np.float32)
    base_vh_db = base_vv_db - rng.uniform(5.0, 8.0, size=(h, w)).astype(np.float32)

    # A few "ships" / structures (high backscatter spots)
    n_objects = rng.integers(2, 6)
    for _ in range(n_objects):
        cy = int(rng.integers(20, h - 20))
        cx = int(rng.integers(20, w - 20))
        r = int(rng.integers(3, 10))
        ys, xs = np.ogrid[:h, :w]
        mask = (ys - cy) ** 2 + (xs - cx) ** 2 <= r ** 2
        base_vv_db[mask] = rng.uniform(-5.0, 3.0)
        base_vh_db[mask] = rng.uniform(-12.0, -5.0)

    # Convert dB → linear amplitude (the decoder expects raw amplitude or dB,
    # detects which based on min value sign).
    vv_amp = (10.0 ** (base_vv_db / 10.0)).astype(np.float32)
    vh_amp = (10.0 ** (base_vh_db / 10.0)).astype(np.float32)
    return np.stack([vv_amp, vh_amp], axis=0)


def _save_sar_tif(out_path: Path, arr: np.ndarray) -> None:
    """Write 2-band float32 GeoTIFF compatible with sar.decode_s1grd."""
    import rasterio
    h, w = arr.shape[1], arr.shape[2]
    with rasterio.open(
        out_path, "w",
        driver="GTiff", height=h, width=w, count=2, dtype="float32",
        compress="deflate",
    ) as dst:
        dst.write(arr)


def _ensure_dataset(dataset_dir: Path) -> None:
    """Generate synthetic SAR chips if labels.json is missing or empty."""
    labels_path = dataset_dir / _LABELS_FNAME
    if labels_path.exists():
        try:
            existing = json.loads(labels_path.read_text())
            if len(existing) >= 5:
                log.debug("SAR synth dataset already populated (%d entries) — skipping", len(existing))
                return
        except json.JSONDecodeError:
            pass

    chips_dir = dataset_dir / "chips"
    chips_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed=2026)
    records = []
    for i in range(_N_SYNTHETIC):
        arr = _generate_chip(rng)
        chip_name = f"sar_{i:04d}.tif"
        chip_path = chips_dir / chip_name
        _save_sar_tif(chip_path, arr)
        records.append({
            "chip_file": f"chips/{chip_name}",
            "modality": "sar",
            "source": "synthetic_2band_dB_v1",
            "ground_truth": {},
            "annotations": [],
        })
    labels_path.write_text(json.dumps(records, indent=2))
    log.info("Generated %d synthetic SAR chips → %s", _N_SYNTHETIC, dataset_dir)


def iter_sar_synth(
    labels_path: str | None = None,
    max_chips: int | None = None,
) -> Generator[tuple[bytes, str, list[str], dict], None, None]:
    """Yield (chip_bytes, "sar", prompts, ground_truth) tuples for the
    synthetic SAR slice.
    """
    if labels_path is None:
        dataset_dir = _DEFAULT_DATASET_DIR
        resolved = dataset_dir / _LABELS_FNAME
    else:
        resolved = Path(labels_path).resolve()
        dataset_dir = resolved.parent

    if not resolved.exists():
        log.warning("SAR synth labels.json not found at %s — yielding nothing", resolved)
        return

    records: list[dict] = json.loads(resolved.read_text(encoding="utf-8"))
    base_dir = dataset_dir.resolve()
    count = 0
    for record in records:
        if max_chips is not None and count >= max_chips:
            break
        chip_rel = record["chip_file"]
        chip_path = (base_dir / chip_rel).resolve()
        if not str(chip_path).startswith(str(base_dir)):
            log.warning("Path traversal — skipping %s", chip_rel)
            continue
        if not chip_path.exists():
            log.warning("Chip missing — skipping %s", chip_path)
            continue
        chip_bytes = chip_path.read_bytes()
        # Generic prompts so SAM3 has something to detect in the preview.
        prompts = ["ship", "vessel", "boat"]
        yield chip_bytes, "sar", prompts, {}
        count += 1
