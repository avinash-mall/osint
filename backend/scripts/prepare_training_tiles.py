#!/usr/bin/env python3
"""Chip-aligned training-tile preprocessor.

Inference chips every raster with ``plan_inference_grid`` (chip_size=1008,
overlap=252 by default) before running detection. If the detector is fine-tuned
on whole images that are far larger than a chip, the train and inference pixel
distributions diverge: at inference time the model only ever sees 1008 px
windows, never the full scene. This module cuts training tiles with the SAME
planner the inference worker uses so the two distributions match.

Given a YOLO-format dataset (images/ + labels/ + data.yaml), it:

  * tiles every image using ``plan_inference_grid`` (imported, not duplicated —
    HARD RULE: one chip planner), one output image per tile,
  * rewrites each YOLO bbox into tile-local normalised coordinates, CLIPPING
    boxes to the tile and DROPPING boxes whose visible area inside the tile
    falls below ``min_visibility`` (default 0.20) — the standard SAHI-style
    tiling rule,
  * passes images already <= chip_size straight through as a single tile
    (the planner returns one full-size window, so the geometry is identity).

Output is a fresh YOLO dataset (images/{split}, labels/{split}, data.yaml)
ready for the /train endpoint.

Runs in the host venv (CPU only): PIL + the pure-Python planner. No torch.

CLI:
    python -m scripts.prepare_training_tiles \\
        --src /data/datasets/mvrsd --out /data/datasets/mvrsd-tiled \\
        [--chip-size 1008] [--overlap 252] [--min-visibility 0.2]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

# One chip planner for the whole stack (HARD RULE: do not duplicate). The
# inference defaults live next to it so train/inference stay in lockstep.
from worker_legacy import (  # noqa: E402
    DEFAULT_INFERENCE_CHIP_SIZE,
    DEFAULT_INFERENCE_OVERLAP,
    plan_inference_grid,
)

IMG_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp")


def _read_yolo_label(label_path: Path) -> list[tuple[int, float, float, float, float]]:
    """Parse a YOLO label file into (cls, cx, cy, w, h) normalised tuples."""
    if not label_path.is_file():
        return []
    rows: list[tuple[int, float, float, float, float]] = []
    for raw in label_path.read_text().splitlines():
        parts = raw.split()
        if len(parts) < 5:
            continue
        try:
            cls = int(float(parts[0]))
            cx, cy, w, h = (float(p) for p in parts[1:5])
        except ValueError:
            continue
        rows.append((cls, cx, cy, w, h))
    return rows


def retile_labels(
    boxes: list[tuple[int, float, float, float, float]],
    img_w: int,
    img_h: int,
    tile_x: int,
    tile_y: int,
    tile_w: int,
    tile_h: int,
    min_visibility: float = 0.20,
) -> list[tuple[int, float, float, float, float]]:
    """Rewrite normalised YOLO boxes into a tile's local normalised frame.

    Boxes are clipped to the tile. A box is kept only if the area visible inside
    the tile is at least ``min_visibility`` of its original area. Returns
    tile-local (cls, cx, cy, w, h) normalised to the tile size.
    """
    out: list[tuple[int, float, float, float, float]] = []
    for cls, cx, cy, bw, bh in boxes:
        # De-normalise to absolute image pixels.
        ax1 = (cx - bw / 2.0) * img_w
        ay1 = (cy - bh / 2.0) * img_h
        ax2 = (cx + bw / 2.0) * img_w
        ay2 = (cy + bh / 2.0) * img_h
        orig_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        if orig_area <= 0:
            continue
        # Clip to the tile window [tile_x, tile_x+tile_w) x [tile_y, ...).
        ix1 = max(ax1, tile_x)
        iy1 = max(ay1, tile_y)
        ix2 = min(ax2, tile_x + tile_w)
        iy2 = min(ay2, tile_y + tile_h)
        if ix2 <= ix1 or iy2 <= iy1:
            continue
        vis_area = (ix2 - ix1) * (iy2 - iy1)
        if vis_area / orig_area < min_visibility:
            continue
        # Re-normalise into tile-local coords.
        lcx = ((ix1 + ix2) / 2.0 - tile_x) / tile_w
        lcy = ((iy1 + iy2) / 2.0 - tile_y) / tile_h
        lw = (ix2 - ix1) / tile_w
        lh = (iy2 - iy1) / tile_h
        out.append((cls, lcx, lcy, lw, lh))
    return out


def _iter_tiles(width: int, height: int, chip_size: int, overlap: int):
    """Yield (tile_x, tile_y, tile_w, tile_h) windows from the shared planner.

    ``max_chips=0`` disables sampling (full coverage); ``block_size=None``
    disables COG block snapping (training images aren't tiled COGs)."""
    grid = plan_inference_grid(width, height, chip_size, overlap, max_chips=0)
    for ty, th in zip(grid["y_offsets"], grid["y_window_sizes"]):
        for tx, tw in zip(grid["x_offsets"], grid["x_window_sizes"]):
            yield tx, ty, tw, th


def tile_image(
    img_path: Path,
    label_path: Path,
    out_img_dir: Path,
    out_lbl_dir: Path,
    chip_size: int,
    overlap: int,
    min_visibility: float,
) -> tuple[int, int]:
    """Tile one image+label pair. Returns (tiles_written, boxes_written).

    Single-tile pass-through (image <= chip_size on both axes) copies the
    original image and label unchanged — the planner returns one full-size
    window, so retile_labels is an identity transform, but we copy to avoid a
    needless re-encode."""
    from PIL import Image

    with Image.open(img_path) as im:
        width, height = im.size
        boxes = _read_yolo_label(label_path)

        tiles = list(_iter_tiles(width, height, chip_size, overlap))
        single = len(tiles) == 1 and tiles[0][2] >= width and tiles[0][3] >= height

        if single:
            out_img = out_img_dir / img_path.name
            shutil.copy2(img_path, out_img)
            lines = [f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for c, cx, cy, w, h in boxes]
            (out_lbl_dir / f"{img_path.stem}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else "")
            )
            return 1, len(boxes)

        im_rgb = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        tiles_written = boxes_written = 0
        for tx, ty, tw, th in tiles:
            local = retile_labels(boxes, width, height, tx, ty, tw, th, min_visibility)
            stem = f"{img_path.stem}_x{tx}_y{ty}"
            crop = im_rgb.crop((tx, ty, tx + tw, ty + th))
            crop.save(out_img_dir / f"{stem}.jpg", quality=95)
            lines = [f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for c, cx, cy, w, h in local]
            (out_lbl_dir / f"{stem}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else "")
            )
            tiles_written += 1
            boxes_written += len(local)
        return tiles_written, boxes_written


def _copy_data_yaml(src: Path, out: Path) -> None:
    """Rewrite data.yaml for the tiled dataset (same classes, new path)."""
    src_yaml = src / "data.yaml"
    if not src_yaml.is_file():
        return
    lines = []
    for raw in src_yaml.read_text().splitlines():
        s = raw.strip()
        if s.startswith("path:"):
            lines.append(f"path: {out}")
        elif s.startswith("train:"):
            lines.append("train: images/train")
        elif s.startswith("val:"):
            lines.append("val: images/val")
        elif s.startswith("test:"):
            continue  # test handled only if present below
        else:
            lines.append(raw)
    (out / "data.yaml").write_text("\n".join(lines) + "\n")


def tile_dataset(
    src: Path,
    out: Path,
    chip_size: int = DEFAULT_INFERENCE_CHIP_SIZE,
    overlap: int = DEFAULT_INFERENCE_OVERLAP,
    min_visibility: float = 0.20,
    splits: tuple[str, ...] = ("train", "val"),
) -> dict:
    """Tile an entire YOLO dataset. Returns a per-split summary dict."""
    out.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "chip_size": chip_size,
        "overlap": overlap,
        "min_visibility": min_visibility,
        "splits": {},
    }
    for split in splits:
        src_img_dir = src / "images" / split
        src_lbl_dir = src / "labels" / split
        if not src_img_dir.is_dir():
            continue
        out_img_dir = out / "images" / split
        out_lbl_dir = out / "labels" / split
        out_img_dir.mkdir(parents=True, exist_ok=True)
        out_lbl_dir.mkdir(parents=True, exist_ok=True)

        imgs_in = tiles_out = boxes_out = 0
        for img in sorted(src_img_dir.iterdir()):
            if img.suffix.lower() not in IMG_EXTS:
                continue
            imgs_in += 1
            t, b = tile_image(
                img, src_lbl_dir / f"{img.stem}.txt",
                out_img_dir, out_lbl_dir,
                chip_size, overlap, min_visibility,
            )
            tiles_out += t
            boxes_out += b
        summary["splits"][split] = {
            "images_in": imgs_in, "tiles_out": tiles_out, "boxes_out": boxes_out,
        }
    _copy_data_yaml(src, out)
    return summary


def _main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--src", required=True, type=Path, help="Source YOLO dataset root")
    p.add_argument("--out", required=True, type=Path, help="Output tiled dataset root")
    p.add_argument("--chip-size", type=int, default=DEFAULT_INFERENCE_CHIP_SIZE)
    p.add_argument("--overlap", type=int, default=DEFAULT_INFERENCE_OVERLAP)
    p.add_argument("--min-visibility", type=float, default=0.20)
    args = p.parse_args(argv)

    summary = tile_dataset(
        args.src, args.out,
        chip_size=args.chip_size, overlap=args.overlap,
        min_visibility=args.min_visibility,
    )
    import json
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
