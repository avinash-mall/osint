#!/usr/bin/env python3
"""Stage the MVRSD (Military Vehicle Remote Sensing Dataset) as a YOLO dataset.

MVRSD ships as 3,000 Google-Earth chips (640x640, 0.3 m GSD) over 40+ military
scenarios across Asia / North America / Europe, plus 3 large unlabeled test
rasters. 32,626 labelled instances across 5 vehicle classes:

    0 SMV   small military vehicle
    1 LMV   large military vehicle
    2 AFV   armoured fighting vehicle
    3 CV    civilian vehicle
    4 MCV   military construction vehicle

The full imagery is account-locked (Baidu Cloud / SciDB) and CANNOT be fetched
programmatically — see scripts/manifests/mvrsd.json and the operator runbook.

This script stages whatever real imagery is present from one of two sources,
in priority order:

  1. A drop-in tree the operator extracted from the official Baidu/SciDB
     archive (the full 3,000-image dataset). Expected layout:
         <dropin>/mvrsd/images/{train,val}/<stem>.jpg
         <dropin>/mvrsd/labels/{train,val}/<stem>.txt   (YOLO) OR
         <dropin>/mvrsd/labels/{train,val}/xml/<stem>.xml (Pascal VOC)
  2. The official repo's demo.zip (12 images) extracted to a directory, paired
     with the community YOLO label port (full 3,002 .txt label set) when the
     latter is supplied via --community-labels.

It writes the canonical YOLO layout the /train endpoint consumes:

    <out>/images/train/*.jpg   <out>/labels/train/*.txt
    <out>/images/val/*.jpg     <out>/labels/val/*.txt
    <out>/data.yaml

Pascal-VOC XML labels are converted to YOLO txt using the canonical class order
above. Images without a matching label file are skipped (loudly).

Runs in the host venv (CPU only) — no torch/ultralytics needed.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Canonical class order. This is MVRSD's classes.txt order, which is what the
# label indices in both the official XML->name mapping and the community YOLO
# txt port actually encode. NOTE: the community repo's data.yaml `names` list
# is in a DIFFERENT order and is inconsistent with its own label indices — we
# deliberately do NOT use it. See scripts/manifests/mvrsd.json.
MVRSD_CLASSES = ["SMV", "LMV", "AFV", "CV", "MCV"]
_CLASS_TO_IDX = {name: i for i, name in enumerate(MVRSD_CLASSES)}

IMG_EXTS = (".jpg", ".jpeg", ".png")


def _voc_xml_to_yolo(xml_path: Path) -> list[str]:
    """Convert one Pascal-VOC XML to YOLO-normalised lines. Empty list on parse
    failure or unknown classes (skipped individually)."""
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return []
    size = root.find("size")
    if size is None:
        return []
    w = float(size.findtext("width") or 0)
    h = float(size.findtext("height") or 0)
    if w <= 0 or h <= 0:
        return []
    lines: list[str] = []
    for obj in root.findall("object"):
        name = (obj.findtext("name") or "").strip()
        if name not in _CLASS_TO_IDX:
            continue
        box = obj.find("bndbox")
        if box is None:
            continue
        try:
            x1 = float(box.findtext("xmin"))
            y1 = float(box.findtext("ymin"))
            x2 = float(box.findtext("xmax"))
            y2 = float(box.findtext("ymax"))
        except (TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        cx = (x1 + x2) / 2.0 / w
        cy = (y1 + y2) / 2.0 / h
        bw = (x2 - x1) / w
        bh = (y2 - y1) / h
        lines.append(f"{_CLASS_TO_IDX[name]} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return lines


def _resolve_label(stem: str, split: str, label_dirs: list[Path]) -> list[str] | None:
    """Find a YOLO .txt or VOC .xml label for ``stem`` in the candidate dirs.
    Returns YOLO lines, or None when no label is found."""
    for d in label_dirs:
        txt = d / f"{stem}.txt"
        if txt.is_file():
            return [ln for ln in txt.read_text().splitlines() if ln.strip()]
        xml = d / f"{stem}.xml"
        if xml.is_file():
            return _voc_xml_to_yolo(xml)
        xml2 = d / "xml" / f"{stem}.xml"
        if xml2.is_file():
            return _voc_xml_to_yolo(xml2)
    return None


def _collect_split(
    image_root: Path,
    split: str,
    label_dirs: list[Path],
    out: Path,
) -> tuple[int, int]:
    """Stage one split. Returns (images_written, images_skipped_no_label)."""
    src_img_dir = image_root / split
    if not src_img_dir.is_dir():
        return 0, 0
    out_img = out / "images" / split
    out_lbl = out / "labels" / split
    out_img.mkdir(parents=True, exist_ok=True)
    out_lbl.mkdir(parents=True, exist_ok=True)

    written = skipped = 0
    for img in sorted(src_img_dir.iterdir()):
        if img.suffix.lower() not in IMG_EXTS:
            continue
        lines = _resolve_label(img.stem, split, label_dirs)
        if lines is None:
            skipped += 1
            print(f"  WARN no label for {split}/{img.name} — skipping", file=sys.stderr)
            continue
        shutil.copy2(img, out_img / img.name)
        (out_lbl / f"{img.stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        written += 1
    return written, skipped


def _write_data_yaml(out: Path, has_test: bool) -> None:
    names = "[ " + ", ".join(f"'{c}'" for c in MVRSD_CLASSES) + " ]"
    lines = [
        "# MVRSD — Military Vehicle Remote Sensing Dataset (staged by stage_mvrsd.py)",
        "# Class order is MVRSD classes.txt order — see scripts/manifests/mvrsd.json.",
        f"path: {out}",
        "train: images/train",
        "val: images/val",
    ]
    if has_test:
        lines.append("test: images/test")
    lines += [f"nc: {len(MVRSD_CLASSES)}", f"names: {names}", ""]
    (out / "data.yaml").write_text("\n".join(lines))


def stage(
    out: Path,
    dropin: Path | None,
    demo_images: Path | None,
    community_labels: Path | None,
) -> dict:
    """Stage MVRSD into ``out`` from the best available real source."""
    out.mkdir(parents=True, exist_ok=True)

    # Source 1: operator drop-in (full dataset).
    if dropin is not None and (dropin / "mvrsd" / "images").is_dir():
        image_root = dropin / "mvrsd" / "images"
        lbl_root = dropin / "mvrsd" / "labels"
        source = f"drop-in {dropin / 'mvrsd'}"
        label_dirs_for = lambda split: [lbl_root / split]  # noqa: E731
    # Source 2: demo.zip images + community YOLO label port.
    elif demo_images is not None and (demo_images / "train").is_dir():
        image_root = demo_images
        source = f"demo images {demo_images}"
        # Community labels live flat under labels/{train,val}; the demo XML lives
        # under demo's own labels/{train,val}/xml. Both are tried per split.
        comm = community_labels
        demo_lbl = demo_images.parent / "labels"

        def label_dirs_for(split: str) -> list[Path]:  # type: ignore[misc]
            dirs: list[Path] = []
            if comm is not None:
                dirs.append(comm / split)
            dirs.append(demo_lbl / split)  # XML fallback (has xml/ subdir)
            return dirs
    else:
        raise RuntimeError(
            "no real MVRSD imagery found: supply --dropin <root> (full dataset) "
            "or --demo-images <demo_extract/images> [--community-labels <repo>/data/labels]"
        )

    result = {"source": source, "classes": MVRSD_CLASSES, "splits": {}}
    for split in ("train", "val"):
        w, s = _collect_split(image_root, split, label_dirs_for(split), out)
        result["splits"][split] = {"written": w, "skipped_no_label": s}

    _write_data_yaml(out, has_test=(out / "images" / "test").is_dir())
    return result


def _main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True, type=Path, help="Output YOLO dataset root")
    p.add_argument("--dropin", type=Path, default=None,
                   help="Operator drop-in root containing mvrsd/images + mvrsd/labels")
    p.add_argument("--demo-images", type=Path, default=None,
                   help="Path to demo.zip-extracted images/ dir (train/ + val/)")
    p.add_argument("--community-labels", type=Path, default=None,
                   help="Path to community YOLO label port (data/labels with train/ + val/)")
    args = p.parse_args(argv)

    res = stage(args.out, args.dropin, args.demo_images, args.community_labels)
    import json
    print(json.dumps(res, indent=2, default=str))
    total = sum(s["written"] for s in res["splits"].values())
    if total == 0:
        print("ERROR staged 0 images", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
