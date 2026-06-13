#!/usr/bin/env python3
"""Download real-world test slices for the inference layer comparison.

Pulls:
- Real DOTA-v1.0 val slice (Last-Bullet/DOTAv1.0)  -> inference-sam3/eval/datasets/dota/

Replaces the synthetic chips/labels.json that fetch_eval_datasets.py creates.
Idempotent on a per-dataset basis: skip if labels.json already references >=N
real-marked records.

Requires HF_TOKEN env var (read from .env if present).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parent.parent
DOTA_OUT = REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "dota"

# DOTA-v1.0 class names (matches Ultralytics' DOTA-v1 conventions).
DOTA_CLASSES = {
    "plane", "ship", "storage-tank", "baseball-diamond", "tennis-court",
    "basketball-court", "ground-track-field", "harbor", "bridge",
    "large-vehicle", "small-vehicle", "helicopter", "roundabout",
    "soccer-ball-field", "swimming-pool", "container-crane",
    # v1.5 extras present in some files:
    "airport", "helipad",
}


def _load_hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("HF_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("HF_TOKEN not set")


def _parse_dota_label(text: str, img_w: int, img_h: int) -> list[dict]:
    """Parse DOTA labelTxt. Each line: x1 y1 x2 y2 x3 y3 x4 y4 class difficult"""
    out = []
    for raw in text.splitlines():
        parts = raw.strip().split()
        if len(parts) < 9:
            continue
        try:
            coords = [float(p) for p in parts[:8]]
        except ValueError:
            continue
        cls = parts[8].strip()
        # axis-aligned bbox = min/max of the 4 OBB corners
        xs, ys = coords[0::2], coords[1::2]
        x1, x2 = max(0, int(min(xs))), min(img_w, int(max(xs)))
        y1, y2 = max(0, int(min(ys))), min(img_h, int(max(ys)))
        if x2 <= x1 or y2 <= y1:
            continue
        out.append({"label": cls, "bbox_xyxy": [x1, y1, x2, y2]})
    return out


def _already_complete(out_dir: Path, min_records: int) -> bool:
    """Per-dataset idempotency (matches the module docstring): True when
    labels.json already references at least ``min_records`` records, so a re-run
    is a no-op. Returns False on any read/parse error so a corrupt file refetches.
    """
    labels = out_dir / "labels.json"
    if not labels.exists():
        return False
    try:
        recs = json.loads(labels.read_text())
    except Exception:
        return False
    return isinstance(recs, list) and len(recs) >= min_records


def fetch_dota(max_chips: int = 30, force: bool = False) -> None:
    """Download DOTA-v1.0 val slice with real labels."""
    if not force and _already_complete(DOTA_OUT, max_chips):
        print(f"[fetch_real] DOTA already complete (labels.json ≥ {max_chips} records) — skipping. Use --force to refetch.")
        return
    print(f"[fetch_real] Downloading DOTA-v1.0 val slice (max {max_chips}) ...")
    DOTA_OUT.mkdir(parents=True, exist_ok=True)
    chips_dir = DOTA_OUT / "chips"
    chips_dir.mkdir(exist_ok=True)

    from huggingface_hub import HfApi, hf_hub_download
    token = _load_hf_token()
    api = HfApi(token=token)
    info = api.dataset_info("Last-Bullet/DOTAv1.0")
    val_imgs = sorted(s.rfilename for s in info.siblings
                      if s.rfilename.startswith("DOTA_V1.0/val/images/")
                      and s.rfilename.endswith(".png"))[:max_chips]
    print(f"[fetch_real]   selecting {len(val_imgs)} chips from {len([s for s in info.siblings if s.rfilename.startswith('DOTA_V1.0/val/images/')])} available")

    records = []
    for i, img_path in enumerate(val_imgs, 1):
        chip_name = Path(img_path).stem  # e.g. "P0003"
        label_path = f"DOTA_V1.0/val/labelTxt/{chip_name}.txt"
        try:
            local_img = hf_hub_download("Last-Bullet/DOTAv1.0", img_path,
                                        repo_type="dataset", token=token)
            local_label = hf_hub_download("Last-Bullet/DOTAv1.0", label_path,
                                          repo_type="dataset", token=token)
        except Exception as exc:
            print(f"[fetch_real]   skip {chip_name}: {exc}")
            continue

        # Copy chip into our dataset dir (rename to sequential numbering).
        chip_out = chips_dir / f"{chip_name}.png"
        if not chip_out.exists():
            import shutil
            shutil.copy(local_img, chip_out)

        with Image.open(chip_out) as img:
            w, h = img.size
        annotations = _parse_dota_label(Path(local_label).read_text(), w, h)
        records.append({
            "chip_file": f"chips/{chip_name}.png",
            "modality": "rgb",
            "source": "dota_v1.0_val",
            "annotations": annotations,
        })
        if i % 5 == 0:
            print(f"[fetch_real]   {i}/{len(val_imgs)} ({chip_name}, {w}x{h}, {len(annotations)} GT boxes)")

    # Write labels.json
    (DOTA_OUT / "labels.json").write_text(json.dumps(records, indent=2))
    total_boxes = sum(len(r["annotations"]) for r in records)
    print(f"[fetch_real] DOTA done: {len(records)} chips, {total_boxes} GT boxes -> {DOTA_OUT}")


def main() -> int:
    import argparse
    # Phase 9.42: bump default DOTA slice from 30 -> 200 chips so per-class
    # AP measurements have enough instances to be statistically meaningful
    # for the 6 sparse classes (military_forces, armored_vehicle, logistics,
    # civilian, other, etc.). The old 30-chip slice produced flat-zero
    # recall on those classes because they had < 2 instances per class.
    parser = argparse.ArgumentParser(description="Fetch real eval datasets for inference QA.")
    parser.add_argument("--skip-dota", action="store_true")
    parser.add_argument("--dota-chips", type=int, default=200,
                        help="DOTA-v1.0 validation chips to fetch (default 200; bumped from 30 in Phase 9.42).")
    parser.add_argument("--force", action="store_true",
                        help="Refetch even when labels.json already has enough records (overrides per-dataset idempotency).")
    args = parser.parse_args()
    if not args.skip_dota:
        fetch_dota(max_chips=args.dota_chips, force=args.force)
    print("[fetch_real] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
