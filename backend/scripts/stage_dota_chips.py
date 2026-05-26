"""Stage DOTA chips into the per-class layout the baker expects.

For each entry in labels.json:
- Pick the annotation with the largest bbox area (tie-break: first listed).
- Crop the chip to that bbox + 8 px margin (clipped to image bounds).
- Save the crop as <out_root>/<class>/<chip-stem>__<idx>.png.

This makes every chip carry one canonical class assignment without re-extracting
from the full DOTA training rasters. The result is one subdirectory per class
under <out_root>, ready for `bake_reference_index --dataset dota`.

Idempotent: re-runs overwrite existing files of the same name.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image


def stage(labels_json: Path, chips_dir: Path, out_root: Path, margin_px: int = 8) -> dict[str, int]:
    rows = json.loads(labels_json.read_text())
    counts: dict[str, int] = {}
    for row in rows:
        anns = row.get("annotations") or []
        if not anns:
            continue
        chip_path = chips_dir / row["chip_file"]
        if not chip_path.is_file():
            continue
        # Pick largest-area annotation
        def _area(a):
            x1, y1, x2, y2 = a["bbox_xyxy"]
            return max(0.0, x2 - x1) * max(0.0, y2 - y1)
        best = max(anns, key=_area)
        cls = best["label"]
        x1, y1, x2, y2 = best["bbox_xyxy"]
        with Image.open(chip_path) as img:
            w, h = img.size
            l = max(0, int(x1) - margin_px)
            t = max(0, int(y1) - margin_px)
            r = min(w, int(x2) + margin_px)
            b = min(h, int(y2) + margin_px)
            if r - l < 8 or b - t < 8:
                continue
            crop = img.crop((l, t, r, b))
            (out_root / cls).mkdir(parents=True, exist_ok=True)
            crop.save(out_root / cls / f"{chip_path.stem}__{cls}.png")
            counts[cls] = counts.get(cls, 0) + 1
    return counts


def _main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", required=True)
    p.add_argument("--chips-dir", required=True)
    p.add_argument("--out-root", required=True)
    p.add_argument("--margin-px", type=int, default=8)
    args = p.parse_args(argv)
    counts = stage(Path(args.labels), Path(args.chips_dir), Path(args.out_root), margin_px=args.margin_px)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
