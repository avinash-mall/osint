#!/usr/bin/env python3
"""
One-time, idempotent fetcher for evaluation datasets used by the inference-layer comparison.

DOTA-v1.0 val slice
--------------------
Primary: downloads up to 30 RGB chips from the HuggingFace dataset
``keremberke/satellite-object-detection`` (DOTA format) and saves them to
``inference-sam3/eval/datasets/dota/chips/`` with a ``labels.json``.

Fallback: if the HuggingFace dataset is unavailable or times out, generates
30 SYNTHETIC DOTA-style chips with known bounding boxes so the pipeline works
in CI without network access.

Use --real to get instructions for downloading real DOTA imagery (manual
registration required at https://captain-whu.github.io/DOTA/dataset.html).

Inria fallback
--------------
When DOTA is unavailable (the common case), this script converts the first
``MAX_CHIPS`` GeoTIFF files from /nvme/osint/sample/ to PNG and writes
**synthetic** ground-truth annotations to:
    inference-sam3/eval/datasets/inria_fallback/chips.json

The synthetic annotations are coarse placeholders (one "large-vehicle" box
covering the full image).  They are only meant to exercise the evaluation
pipeline end-to-end, NOT to measure real recall/precision.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
_EVAL_DIR = _REPO_ROOT / "inference-sam3" / "eval" / "datasets"
_DOTA_DIR = _EVAL_DIR / "dota_val"        # legacy (iter_samples)
_DOTA_NEW_DIR = _EVAL_DIR / "dota"        # new format (iter_dota)
_DOTA_CHIPS_DIR = _DOTA_NEW_DIR / "chips"
_DOTA_LABELS = _DOTA_NEW_DIR / "labels.json"
_INRIA_DIR = _EVAL_DIR / "inria_fallback"
_INRIA_JSON = _INRIA_DIR / "chips.json"
_INRIA_SOURCE = Path("/nvme/osint/sample")
MAX_CHIPS = 30
_HF_DATASET = "keremberke/satellite-object-detection"
_HF_SUBSET = "dota"
_HF_TIMEOUT_S = 60

# ---------------------------------------------------------------------------
# DOTA constants
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

# Consistent colour per class (RGB) — generated once so chips are reproducible
_CLASS_COLOURS: dict[str, tuple[int, int, int]] = {
    "plane":               (220,  50,  50),
    "ship":                ( 50, 100, 200),
    "storage-tank":        (180, 100,  30),
    "baseball-diamond":    ( 30, 180,  30),
    "tennis-court":        (200, 200,  50),
    "basketball-court":    (200, 100, 200),
    "ground-track-field":  ( 50, 200, 180),
    "harbor":              ( 80,  80, 220),
    "bridge":              (160, 160, 160),
    "large-vehicle":       (240, 120,  20),
    "small-vehicle":       (240, 200,  20),
    "helicopter":          (200,  50, 200),
    "roundabout":          ( 50, 220, 220),
    "soccer-ball-field":   ( 20, 160,  20),
    "swimming-pool":       ( 20, 180, 240),
    "container-crane":     (160,  80,  40),
    "airport":             (100,  50, 150),
    "helipad":             (220, 160,  50),
}

# ---------------------------------------------------------------------------
# Synthetic DOTA generator
# ---------------------------------------------------------------------------

def generate_synthetic_dota(
    output_dir: Path | None = None,
    n_chips: int = 10,
    chip_size: int = 1024,
    seed: int = 42,
) -> None:
    """
    Generate synthetic DOTA-style chips and a labels.json ground-truth file.

    Creates ``n_chips`` PNG images of size ``chip_size × chip_size`` with
    2-6 random bounding boxes drawn as coloured rectangles, plus a
    ``labels.json`` describing all boxes.

    Parameters
    ----------
    output_dir:
        Directory to write chips and labels.json.  Defaults to
        ``inference-sam3/eval/datasets/dota_val/``.
    n_chips:
        Number of chips to generate (default 10).
    chip_size:
        Width and height of each chip in pixels (default 1024).
    seed:
        RNG seed for reproducibility.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise RuntimeError(
            "Pillow is required to generate synthetic chips.  "
            "Install it: pip install Pillow"
        )

    if output_dir is None:
        output_dir = _DOTA_DIR
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    labels_path = output_dir / "labels.json"

    # Idempotency check — if all chips AND labels exist, skip.
    chip_names = [f"chip_{i:03d}.png" for i in range(n_chips)]
    all_exist = labels_path.exists() and all((output_dir / n).exists() for n in chip_names)
    if all_exist:
        print(
            f"[fetch_eval_datasets] Synthetic DOTA chips already present at {output_dir} — skipping."
        )
        return

    rng = random.Random(seed)
    all_records: list[dict] = []

    for i in range(n_chips):
        chip_name = chip_names[i]
        chip_path = output_dir / chip_name

        # Background: dark grey with slight noise to look non-trivial
        bg_value = rng.randint(30, 70)
        img = Image.new("RGB", (chip_size, chip_size), color=(bg_value, bg_value, bg_value))
        draw = ImageDraw.Draw(img)

        # Add faint grid lines to simulate satellite texture
        grid_spacing = rng.randint(80, 160)
        line_colour = (bg_value + 15, bg_value + 15, bg_value + 15)
        for x in range(0, chip_size, grid_spacing):
            draw.line([(x, 0), (x, chip_size)], fill=line_colour, width=1)
        for y in range(0, chip_size, grid_spacing):
            draw.line([(0, y), (chip_size, y)], fill=line_colour, width=1)

        # Random boxes
        n_boxes = rng.randint(2, 6)
        boxes: list[dict] = []
        for _ in range(n_boxes):
            label = rng.choice(DOTA_CLASSES)
            # Box size varies by class (some objects are tiny, some large)
            min_side, max_side = _class_size_range(label)
            bw = rng.randint(min_side, max_side)
            bh = rng.randint(min_side, max_side)
            x1 = rng.randint(0, chip_size - bw - 1)
            y1 = rng.randint(0, chip_size - bh - 1)
            x2, y2 = x1 + bw, y1 + bh

            colour = _CLASS_COLOURS[label]
            # Draw filled rectangle with slight transparency feel (outline + fill)
            draw.rectangle([x1, y1, x2, y2], fill=_darken(colour, 0.4), outline=colour, width=2)

            boxes.append(
                {
                    "label": label,
                    "bbox_xyxy": [x1, y1, x2, y2],
                    "difficulty": rng.choice([0, 0, 0, 1]),  # mostly easy
                }
            )

        img.save(chip_path, format="PNG")
        all_records.append({"chip": chip_name, "boxes": boxes})
        print(f"[fetch_eval_datasets] Generated {chip_name} ({n_boxes} boxes)")

    with labels_path.open("w") as fh:
        json.dump(all_records, fh, indent=2)

    print(
        f"[fetch_eval_datasets] Wrote {n_chips} synthetic DOTA chips + labels.json → {output_dir}"
    )


def _class_size_range(label: str) -> tuple[int, int]:
    """Return (min_side, max_side) in pixels for a DOTA class."""
    large = {"airport", "ground-track-field", "soccer-ball-field", "harbor", "bridge"}
    medium = {"basketball-court", "tennis-court", "baseball-diamond", "roundabout",
              "swimming-pool", "storage-tank"}
    if label in large:
        return 200, 500
    if label in medium:
        return 80, 200
    # small objects
    return 20, 80


def _darken(colour: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Return a darkened version of *colour* by *factor* (0=black, 1=original)."""
    return tuple(int(c * factor) for c in colour)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_image_lib():
    """Return (rasterio_available, pil_Image).  Prefer rasterio for GeoTIFF."""
    try:
        import rasterio  # noqa: F401
        return True, None
    except ImportError:
        pass
    try:
        from PIL import Image
        return False, Image
    except ImportError:
        raise RuntimeError(
            "Neither rasterio nor PIL (Pillow) is installed.  "
            "Install one of them: pip install rasterio  OR  pip install Pillow"
        )


def _tif_to_png_rasterio(tif_path: Path, png_path: Path) -> tuple[int, int]:
    """Convert GeoTIFF → PNG via rasterio.  Returns (width, height)."""
    import numpy as np
    import rasterio
    from PIL import Image

    with rasterio.open(tif_path) as ds:
        if ds.count >= 3:
            r = ds.read(1)
            g = ds.read(2)
            b = ds.read(3)
            arr = np.stack([r, g, b], axis=-1)
        else:
            band = ds.read(1)
            arr = np.stack([band, band, band], axis=-1)
        h, w = arr.shape[:2]
        img = Image.fromarray(arr.astype("uint8"), mode="RGB")
        img.save(png_path, format="PNG")
    return w, h


def _tif_to_png_pil(tif_path: Path, png_path: Path, Image) -> tuple[int, int]:
    """Convert GeoTIFF → PNG via PIL (single-band or multi-band)."""
    img = Image.open(tif_path)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    img.save(png_path, format="PNG")
    return w, h


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _fetch_inria_fallback(max_chips: int = MAX_CHIPS) -> None:
    """
    Convert up to *max_chips* Inria GeoTIFF files from /nvme/osint/sample/ to
    PNG and write synthetic ground-truth annotations to chips.json.

    Annotations are intentionally coarse (full-image bounding box labelled
    "large-vehicle") and exist solely to exercise the evaluation pipeline.
    """
    _INRIA_DIR.mkdir(parents=True, exist_ok=True)

    if not _INRIA_SOURCE.exists():
        print(
            f"[fetch_eval_datasets] WARNING: Inria source directory not found: {_INRIA_SOURCE}\n"
            "  Cannot generate fallback chips.",
            file=sys.stderr,
        )
        return

    tif_files = sorted(_INRIA_SOURCE.glob("*.tif"))[:max_chips]
    if not tif_files:
        print(
            f"[fetch_eval_datasets] WARNING: No .tif files found in {_INRIA_SOURCE}",
            file=sys.stderr,
        )
        return

    use_rasterio, pil_Image = _import_image_lib()

    records: list[dict] = []
    for tif_path in tif_files:
        png_path = _INRIA_DIR / (tif_path.stem + ".png")
        if not png_path.exists():
            print(f"[fetch_eval_datasets] Converting {tif_path.name} → {png_path.name} …")
            if use_rasterio:
                w, h = _tif_to_png_rasterio(tif_path, png_path)
            else:
                w, h = _tif_to_png_pil(tif_path, png_path, pil_Image)
        else:
            if use_rasterio:
                import rasterio
                with rasterio.open(png_path) as ds:
                    w, h = ds.width, ds.height
            else:
                pil_img = pil_Image.open(png_path)
                w, h = pil_img.size

        record = {
            "chip_path": str(png_path.resolve()),
            "modality": "rgb",
            "gt_boxes": [
                {
                    "label": "large-vehicle",
                    "bbox_xyxy": [0, 0, w, h],
                    "source": "synthetic",
                }
            ],
        }
        records.append(record)

    with _INRIA_JSON.open("w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")

    print(
        f"[fetch_eval_datasets] Wrote {len(records)} Inria fallback chips to {_INRIA_JSON}"
    )


def _fetch_hf_dota(max_chips: int = MAX_CHIPS) -> bool:
    """
    Attempt to download chips from the HuggingFace DOTA dataset.

    Saves chips to ``inference-sam3/eval/datasets/dota/chips/`` and writes
    ``labels.json`` in the new ``chip_file`` / ``annotations`` format.

    Returns True on success, False if the dataset is unavailable or times out.
    """
    try:
        import signal

        import datasets as hf_datasets
        from PIL import Image as PILImage
    except ImportError:
        print(
            "[fetch_eval_datasets] HuggingFace 'datasets' or 'Pillow' not installed — "
            "falling back to synthetic chips.",
            file=sys.stderr,
        )
        return False

    def _timeout_handler(signum, frame):
        raise TimeoutError("HuggingFace dataset download timed out")

    _DOTA_CHIPS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Set a hard timeout so CI does not hang
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(_HF_TIMEOUT_S)
        try:
            print(f"[fetch_eval_datasets] Loading HuggingFace dataset {_HF_DATASET!r} …")
            ds = hf_datasets.load_dataset(
                _HF_DATASET,
                _HF_SUBSET,
                split="validation",
                trust_remote_code=True,
            )
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    except (TimeoutError, Exception) as exc:
        print(
            f"[fetch_eval_datasets] HuggingFace download failed ({exc}) — "
            "falling back to synthetic chips.",
            file=sys.stderr,
        )
        return False

    records: list[dict] = []
    saved = 0

    for idx, example in enumerate(ds):
        if saved >= max_chips:
            break

        chip_file = f"chips/{saved:05d}.png"
        chip_path = _DOTA_NEW_DIR / chip_file

        # Save the image (may already be a PIL image or a file path)
        try:
            img = example.get("image") or example.get("img")
            if img is None:
                continue
            if not isinstance(img, PILImage.Image):
                img = PILImage.fromarray(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(chip_path, format="PNG")
        except Exception as exc:
            print(f"[fetch_eval_datasets] Skipping example {idx}: {exc}", file=sys.stderr)
            continue

        # Parse bounding boxes — HF DOTA format uses 'objects' with 'bbox' (COCO xywh)
        objects = example.get("objects") or example.get("annotations") or []
        annotations: list[dict] = []
        for obj in objects:
            label = obj.get("category") or obj.get("label") or obj.get("name") or "unknown"
            bbox = obj.get("bbox")
            if bbox is None:
                continue
            # COCO xywh → xyxy
            if len(bbox) == 4:
                x, y, w, h = [float(v) for v in bbox]
                annotations.append({
                    "label": str(label),
                    "bbox_xyxy": [x, y, x + w, y + h],
                })

        records.append({
            "chip_file": chip_file,
            "modality": "rgb",
            "annotations": annotations,
        })
        saved += 1
        print(f"[fetch_eval_datasets] Saved chip {saved}/{max_chips}: {chip_file}")

    if saved == 0:
        print(
            "[fetch_eval_datasets] HuggingFace dataset yielded 0 usable chips — "
            "falling back to synthetic chips.",
            file=sys.stderr,
        )
        return False

    with _DOTA_LABELS.open("w") as fh:
        json.dump(records, fh, indent=2)

    print(
        f"[fetch_eval_datasets] Wrote {saved} HuggingFace DOTA chips + labels.json → {_DOTA_NEW_DIR}"
    )
    return True


def fetch_dota(max_chips: int = MAX_CHIPS) -> None:
    """
    Idempotent DOTA-v1.0 val slice fetcher (new format).

    Saves chips to ``inference-sam3/eval/datasets/dota/chips/`` with a
    ``labels.json`` using the ``chip_file`` / ``annotations`` schema.

    1. If ``labels.json`` already exists with ≥ 5 entries, skip.
    2. Try downloading from HuggingFace ``keremberke/satellite-object-detection``.
    3. Fall back to synthetic chips if HF is unavailable.
    """
    # Idempotency: skip if labels.json already has enough entries
    if _DOTA_LABELS.exists():
        try:
            with _DOTA_LABELS.open() as fh:
                existing = json.load(fh)
            if isinstance(existing, list) and len(existing) >= 5:
                print(
                    f"[fetch_eval_datasets] labels.json already has {len(existing)} entries — skipping."
                )
                return
        except (json.JSONDecodeError, OSError):
            pass  # corrupt file; regenerate

    _DOTA_CHIPS_DIR.mkdir(parents=True, exist_ok=True)

    # Try HuggingFace first
    if _fetch_hf_dota(max_chips=max_chips):
        return

    # Synthetic fallback
    print("[fetch_eval_datasets] Generating synthetic DOTA chips (fallback) …")
    _generate_synthetic_dota_new_format(output_dir=_DOTA_NEW_DIR, n_chips=max_chips)


def _generate_synthetic_dota_new_format(
    output_dir: Path,
    n_chips: int = MAX_CHIPS,
    chip_size: int = 1024,
    seed: int = 42,
) -> None:
    """
    Generate synthetic DOTA-style chips in the new ``chip_file`` / ``annotations`` format.

    Chips are written to ``output_dir/chips/`` and labels to ``output_dir/labels.json``.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise RuntimeError(
            "Pillow is required to generate synthetic chips.  "
            "Install it: pip install Pillow"
        )

    chips_dir = output_dir / "chips"
    chips_dir.mkdir(parents=True, exist_ok=True)
    labels_path = output_dir / "labels.json"

    rng = random.Random(seed)
    all_records: list[dict] = []

    for i in range(n_chips):
        chip_file = f"chips/{i:05d}.png"
        chip_path = output_dir / chip_file

        bg_value = rng.randint(30, 70)
        img = Image.new("RGB", (chip_size, chip_size), color=(bg_value, bg_value, bg_value))
        draw = ImageDraw.Draw(img)

        grid_spacing = rng.randint(80, 160)
        line_colour = (bg_value + 15, bg_value + 15, bg_value + 15)
        for x in range(0, chip_size, grid_spacing):
            draw.line([(x, 0), (x, chip_size)], fill=line_colour, width=1)
        for y in range(0, chip_size, grid_spacing):
            draw.line([(0, y), (chip_size, y)], fill=line_colour, width=1)

        n_boxes = rng.randint(2, 6)
        annotations: list[dict] = []
        for _ in range(n_boxes):
            label = rng.choice(DOTA_CLASSES)
            min_side, max_side = _class_size_range(label)
            bw = rng.randint(min_side, max_side)
            bh = rng.randint(min_side, max_side)
            x1 = rng.randint(0, chip_size - bw - 1)
            y1 = rng.randint(0, chip_size - bh - 1)
            x2, y2 = x1 + bw, y1 + bh

            colour = _CLASS_COLOURS[label]
            draw.rectangle([x1, y1, x2, y2], fill=_darken(colour, 0.4), outline=colour, width=2)
            annotations.append({
                "label": label,
                "bbox_xyxy": [x1, y1, x2, y2],
            })

        img.save(chip_path, format="PNG")
        all_records.append({
            "chip_file": chip_file,
            "modality": "rgb",
            "annotations": annotations,
        })
        print(f"[fetch_eval_datasets] Generated synthetic chip {i + 1}/{n_chips}: {chip_file}")

    with labels_path.open("w") as fh:
        json.dump(all_records, fh, indent=2)

    print(
        f"[fetch_eval_datasets] Wrote {n_chips} synthetic DOTA chips + labels.json → {output_dir}"
    )


def fetch_dota_val(real: bool = False) -> None:
    """
    DOTA-v1.0 val slice fetcher (legacy dota_val format).

    By default, generates synthetic DOTA-style chips (idempotent, no network).
    Pass ``real=True`` to print instructions for downloading the real dataset.

    Parameters
    ----------
    real:
        If True, print manual-download instructions and return without
        generating synthetic data.
    """
    if real:
        print(
            "\n[fetch_eval_datasets] Real DOTA-v1.0 images require manual download.\n"
            "  1. Register at: https://captain-whu.github.io/DOTA/dataset.html\n"
            "  2. Download the validation set images and annotations.\n"
            f"  3. Place them under: {_DOTA_DIR}\n"
            "     (images in dota_val/images/, annotations in dota_val/labelTxt/)\n"
        )
        return

    generate_synthetic_dota()


def fetch_hls_burn(max_chips: int = MAX_CHIPS) -> None:
    """
    Idempotent HLS Burn Scars dataset fetcher.

    Triggers the synthetic fallback inside ``eval_datasets.hls_burn`` so the
    pipeline is ready without a network call.  Generation is skipped when
    ``labels.json`` already exists with ≥ 5 entries.

    Parameters
    ----------
    max_chips:
        Ignored (the HLS synthetic generator always writes 10 chips).
        Kept for API symmetry with ``fetch_dota``.
    """
    # Inserting scripts/ dir so eval_datasets is importable when running from
    # any working directory.
    _scripts_dir = Path(__file__).resolve().parent
    if str(_scripts_dir) not in sys.path:
        sys.path.insert(0, str(_scripts_dir))

    from eval_datasets.hls_burn import _ensure_dataset, _DEFAULT_DATASET_DIR  # noqa: PLC0415
    print("[fetch_eval_datasets] Ensuring HLS Burn Scars dataset …")
    _ensure_dataset(_DEFAULT_DATASET_DIR)
    print(f"[fetch_eval_datasets] HLS Burn Scars ready at {_DEFAULT_DATASET_DIR}")


def fetch_sen1floods(max_chips: int = MAX_CHIPS) -> None:
    """
    Idempotent Sen1Floods11 dataset fetcher.

    Triggers the synthetic fallback inside ``eval_datasets.sen1floods`` so the
    pipeline is ready without a network call.  Generation is skipped when
    ``labels.json`` already exists with ≥ 5 entries.

    Parameters
    ----------
    max_chips:
        Ignored (the Sen1Floods11 synthetic generator always writes 10 chips).
        Kept for API symmetry with ``fetch_dota``.
    """
    _scripts_dir = Path(__file__).resolve().parent
    if str(_scripts_dir) not in sys.path:
        sys.path.insert(0, str(_scripts_dir))

    from eval_datasets.sen1floods import _ensure_dataset, _DEFAULT_DATASET_DIR  # noqa: PLC0415
    print("[fetch_eval_datasets] Ensuring Sen1Floods11 dataset …")
    _ensure_dataset(_DEFAULT_DATASET_DIR)
    print(f"[fetch_eval_datasets] Sen1Floods11 ready at {_DEFAULT_DATASET_DIR}")


def main() -> None:
    """Entry point: run all fetchers."""
    parser = argparse.ArgumentParser(
        description="Fetch / generate evaluation datasets for the inference-layer comparison."
    )
    parser.add_argument(
        "--real",
        action="store_true",
        default=False,
        help=(
            "Print instructions for downloading REAL DOTA-v1.0 imagery "
            "(requires manual registration).  No synthetic chips are generated."
        ),
    )
    parser.add_argument(
        "--max-chips",
        type=int,
        default=MAX_CHIPS,
        help=f"Maximum number of chips to download/generate (default: {MAX_CHIPS}).",
    )
    args = parser.parse_args()

    print("[fetch_eval_datasets] Starting dataset preparation …")
    if args.real:
        fetch_dota_val(real=True)
    else:
        fetch_dota(max_chips=args.max_chips)
        fetch_hls_burn(max_chips=args.max_chips)
        fetch_sen1floods(max_chips=args.max_chips)
    print("[fetch_eval_datasets] Done.")


if __name__ == "__main__":
    main()
