"""Phase 5.20b smoke harness: run backend.sar_cfar on the sar_synth slice.

Standalone (no /detect needed). Iterates the chips, runs CFAR per band-pair,
reports per-chip detection count + score distribution, writes a markdown
summary suitable for the de-biasing benchmark report.

Usage::

    python3 scripts/eval_sar_cfar.py \\
        --chips inference-sam3/eval/datasets/sar_synth \\
        --threshold-sigma 2.5 \\
        --output docs/eval_2026_05_18/sar_cfar_smoke.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median

import numpy as np
import rasterio

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sar_cfar import detect_ships_cfar  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chips", default="inference-sam3/eval/datasets/sar_synth")
    ap.add_argument("--threshold-sigma", type=float, default=2.5)
    ap.add_argument("--guard-px", type=int, default=4)
    ap.add_argument("--background-px", type=int, default=20)
    ap.add_argument("--min-pixels", type=int, default=4)
    ap.add_argument("--output", default="docs/eval_2026_05_18/sar_cfar_smoke.md")
    args = ap.parse_args()

    chips_dir = Path(args.chips)
    labels_path = chips_dir / "labels.json"
    if not labels_path.exists():
        print(f"[eval_sar_cfar] missing {labels_path}")
        return 2
    records = json.loads(labels_path.read_text(encoding="utf-8"))

    rows: list[dict] = []
    total_dets = 0
    all_confidences: list[float] = []
    for rec in records:
        chip_path = chips_dir / rec["chip_file"]
        if not chip_path.exists():
            rows.append({"chip": rec["chip_file"], "n": 0, "error": "missing"})
            continue
        try:
            with rasterio.open(chip_path) as src:
                vv = src.read(1).astype(np.float32)
                vh = src.read(2).astype(np.float32) if src.count >= 2 else None
        except Exception as exc:
            rows.append({"chip": rec["chip_file"], "n": 0, "error": str(exc)[:80]})
            continue

        dets = detect_ships_cfar(
            vv, vh,
            threshold_sigma=args.threshold_sigma,
            guard_px=args.guard_px,
            background_px=args.background_px,
            min_pixels=args.min_pixels,
        )
        confidences = [float(d["confidence"]) for d in dets]
        db_peaks = [float(d["dB_peak"]) for d in dets]
        rows.append({
            "chip": rec["chip_file"],
            "n": len(dets),
            "median_conf": median(confidences) if confidences else None,
            "max_conf": max(confidences) if confidences else None,
            "median_db": median(db_peaks) if db_peaks else None,
        })
        total_dets += len(dets)
        all_confidences.extend(confidences)

    chips_with_dets = sum(1 for r in rows if r["n"] > 0)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# SAR CFAR smoke — `sar_synth` slice",
        "",
        f"Detector: `backend.sar_cfar.detect_ships_cfar` (Phase 5.20b).",
        f"Threshold: {args.threshold_sigma}σ over local clutter; "
        f"guard={args.guard_px}px, background={args.background_px}px, min_pixels={args.min_pixels}.",
        "",
        "## Aggregate",
        "",
        f"- Chips processed: **{len(rows)}**",
        f"- Chips with ≥ 1 detection: **{chips_with_dets} / {len(rows)}**",
        f"- Total detections: **{total_dets}**",
    ]
    if all_confidences:
        lines.append(f"- Detection confidence — median {median(all_confidences):.3f}, "
                     f"max {max(all_confidences):.3f}")
    lines.extend([
        "",
        "## Per-chip",
        "",
        "| Chip | N | Median conf | Max conf | Median dB peak |",
        "|---|---|---|---|---|",
    ])
    for r in rows:
        if r["n"] == 0:
            lines.append(f"| `{r['chip']}` | 0 | — | — | — |")
        else:
            lines.append(
                f"| `{r['chip']}` | {r['n']} | "
                f"{r['median_conf']:.3f} | {r['max_conf']:.3f} | {r['median_db']:.2f} |"
            )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "Pre-refactor: SAR rasters had no native detector; SAM3 was invoked on",
        "TerraMind pseudo-RGB and produced optical-domain false positives. With",
        "Phase 5.20 (skip SAM3 on SAR) + Phase 5.20b (this CFAR detector), SAR",
        "chips now produce explicit ship-class detections grounded in local clutter",
        "statistics. The numbers above are on synthetic SAR (deterministic noise +",
        "scripted bright targets), so they primarily validate that the detector",
        "*runs end-to-end without crashing*. Real Sentinel-1 GRD validation needs",
        "the off-platform dataset fetch (Phase 9.43).",
    ])
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[eval_sar_cfar] wrote {out}")
    print(f"[eval_sar_cfar] chips={len(rows)} with_dets={chips_with_dets} total={total_dets}")
    return 0 if chips_with_dets >= len(rows) // 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
