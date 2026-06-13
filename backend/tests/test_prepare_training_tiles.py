"""Unit tests for the chip-aligned training-tile preprocessor.

Offline / CPU only — PIL + the pure-Python plan_inference_grid planner, no
torch. Proves:
  * a large image tiles via the SAME planner the inference worker uses and
    boxes are rewritten into tile-local normalised coordinates,
  * a small image (<= chip_size) passes through as a single unchanged tile,
  * boxes are clipped to the tile and dropped below min_visibility.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BACKEND_DIR / "scripts"
for d in (BACKEND_DIR, SCRIPTS_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from prepare_training_tiles import (  # noqa: E402
    retile_labels,
    tile_dataset,
    tile_image,
)


# --- retile_labels: clipping / dropping / re-normalisation -------------------


def test_retile_box_fully_inside_tile():
    # 4000x4000 image, box centred at (1000,1000) abs, 200x200 px.
    # cx=0.25, cy=0.25, w=0.05, h=0.05.
    boxes = [(0, 0.25, 0.25, 0.05, 0.05)]
    # Tile covering [0,2000)x[0,2000): box is fully inside.
    out = retile_labels(boxes, 4000, 4000, 0, 0, 2000, 2000, min_visibility=0.2)
    assert len(out) == 1
    cls, cx, cy, w, h = out[0]
    assert cls == 0
    # abs centre (1000,1000) -> tile-local /2000 = 0.5
    assert cx == pytest.approx(0.5, abs=1e-6)
    assert cy == pytest.approx(0.5, abs=1e-6)
    assert w == pytest.approx(200 / 2000, abs=1e-6)
    assert h == pytest.approx(200 / 2000, abs=1e-6)


def test_retile_box_outside_tile_dropped():
    boxes = [(0, 0.25, 0.25, 0.05, 0.05)]  # abs centre (1000,1000)
    # Tile covering [2000,4000)x[2000,4000): no overlap.
    out = retile_labels(boxes, 4000, 4000, 2000, 2000, 2000, 2000, min_visibility=0.2)
    assert out == []


def test_retile_box_below_min_visibility_dropped():
    # Box spanning the tile boundary so only a sliver is inside.
    # abs box: x in [1900,2100], y in [1000,1200]; tile [0,2000)x[0,4000).
    # cx=2000/4000=0.5, cy=1100/4000=0.275, w=200/4000=0.05, h=200/4000=0.05
    boxes = [(0, 0.5, 0.275, 0.05, 0.05)]
    # Visible width inside tile = 100/200 = 0.5 area -> kept at min_vis 0.2.
    kept = retile_labels(boxes, 4000, 4000, 0, 0, 2000, 4000, min_visibility=0.2)
    assert len(kept) == 1
    # Same box, but raise the bar above 0.5 -> dropped.
    dropped = retile_labels(boxes, 4000, 4000, 0, 0, 2000, 4000, min_visibility=0.6)
    assert dropped == []


# --- tile_image: passthrough vs real tiling ----------------------------------


def test_small_image_passthrough(tmp_path: Path):
    out_img = tmp_path / "img"
    out_lbl = tmp_path / "lbl"
    out_img.mkdir()
    out_lbl.mkdir()
    img_path = tmp_path / "small.jpg"
    Image.new("RGB", (640, 640), (10, 20, 30)).save(img_path)
    lbl_path = tmp_path / "small.txt"
    lbl_path.write_text("2 0.5 0.5 0.1 0.1\n")

    tiles, boxes = tile_image(
        img_path, lbl_path, out_img, out_lbl,
        chip_size=1008, overlap=252, min_visibility=0.2,
    )
    assert tiles == 1
    assert boxes == 1
    # Single tile keeps the original filename and label unchanged.
    assert (out_img / "small.jpg").is_file()
    out_label = (out_lbl / "small.txt").read_text().strip().split()
    assert out_label[0] == "2"
    assert float(out_label[1]) == pytest.approx(0.5, abs=1e-6)
    # Output image dimensions unchanged (passthrough copy).
    with Image.open(out_img / "small.jpg") as im:
        assert im.size == (640, 640)


def test_large_image_tiles_with_planner(tmp_path: Path):
    out_img = tmp_path / "img"
    out_lbl = tmp_path / "lbl"
    out_img.mkdir()
    out_lbl.mkdir()
    # 2304x2304 -> with chip 1008 / overlap 252 (step 756): axis_count
    # = ceil((2304-1008)/756)+1 = ceil(1.714)+1 = 3 -> 3x3 = 9 tiles.
    img_path = tmp_path / "big.jpg"
    Image.new("RGB", (2304, 2304), (0, 0, 0)).save(img_path)
    lbl_path = tmp_path / "big.txt"
    # One box dead-centre of the whole image (abs 1152,1152), 100x100 px.
    lbl_path.write_text("1 0.5 0.5 0.043403 0.043403\n")

    tiles, boxes = tile_image(
        img_path, lbl_path, out_img, out_lbl,
        chip_size=1008, overlap=252, min_visibility=0.2,
    )
    assert tiles == 9, "expected 3x3 chip grid"
    # The centre box must land in at least one tile (it sits in the overlap
    # zone, so it may appear in more than one — that's correct for overlapping
    # inference chips).
    assert boxes >= 1
    # Tiled outputs use the x{tx}_y{ty} naming and are real crops.
    produced = sorted(p.name for p in out_img.iterdir())
    assert len(produced) == 9
    assert all("_x" in n and "_y" in n for n in produced)
    # Each tile image is at most chip_size on a side.
    for p in out_img.iterdir():
        with Image.open(p) as im:
            assert im.size[0] <= 1008 and im.size[1] <= 1008


def test_tile_dataset_end_to_end(tmp_path: Path):
    src = tmp_path / "src"
    (src / "images" / "train").mkdir(parents=True)
    (src / "labels" / "train").mkdir(parents=True)
    (src / "images" / "val").mkdir(parents=True)
    (src / "labels" / "val").mkdir(parents=True)
    src.joinpath("data.yaml").write_text(
        "path: /old\ntrain: images/train\nval: images/val\nnc: 1\nnames: ['veh']\n"
    )
    # One small train image (passthrough), one large val image (tiled).
    Image.new("RGB", (640, 640), (1, 1, 1)).save(src / "images" / "train" / "a.jpg")
    (src / "labels" / "train" / "a.txt").write_text("0 0.5 0.5 0.1 0.1\n")
    Image.new("RGB", (2304, 1008), (2, 2, 2)).save(src / "images" / "val" / "b.jpg")
    (src / "labels" / "val" / "b.txt").write_text("0 0.5 0.5 0.05 0.2\n")

    out = tmp_path / "out"
    summary = tile_dataset(src, out, chip_size=1008, overlap=252, min_visibility=0.2)

    assert summary["splits"]["train"]["images_in"] == 1
    assert summary["splits"]["train"]["tiles_out"] == 1  # passthrough
    # 2304x1008 -> x axis 3 tiles, y axis 1 tile -> 3 tiles.
    assert summary["splits"]["val"]["images_in"] == 1
    assert summary["splits"]["val"]["tiles_out"] == 3
    # data.yaml rewritten with the new path, classes preserved.
    yaml_text = (out / "data.yaml").read_text()
    assert f"path: {out}" in yaml_text
    assert "names: ['veh']" in yaml_text
    assert (out / "images" / "train" / "a.jpg").is_file()
