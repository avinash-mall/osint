#!/usr/bin/env python3
"""
build_triage_set.py
===================
Build a triage benchmark from the most recent ingested imagery.

A "triage set" is a small (~10-50 chip) corpus of representative chips pulled
from the user's own recent ``/api/ingest/upload`` jobs, paired with an
``annotations.yaml`` template that an analyst fills in by hand to mark which
ontology labels they expect on each chip. Every subsequent improvement to the
detection stack is measured by re-running
``compare_inference_layers.py --triage-set <dir>`` and comparing per-class
precision / recall against the prior run.

This script is Tier 0 of the detection-quality plan: without a benchmark on
the user's own imagery, every claimed improvement is unverified.

Usage
-----
::

    # Default: scan /data/imagery/processed for the 50 most recent COGs and
    # write today's triage set under bench/triage/<YYYY-MM-DD>/
    python scripts/build_triage_set.py

    # Custom output and lower chip count for a quick analyst pass
    python scripts/build_triage_set.py \\
      --out bench/triage/2026-05-28 \\
      --max-uploads 20 --chips-per-upload 2

    # Dry-run: print what would be written without touching disk
    python scripts/build_triage_set.py --dry-run

    # API mode (when /data/imagery/processed is not mounted on this host)
    python scripts/build_triage_set.py \\
      --source api \\
      --api-url http://localhost:3000 \\
      --session-cookie "$SENTINEL_SESSION_COOKIE"

After the run, edit ``annotations.yaml`` to mark expected labels per chip,
then run::

    python scripts/compare_inference_layers.py --triage-set <out-dir>
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_triage_set")


# ---------------------------------------------------------------------------
# Discovery — data-dir mode
# ---------------------------------------------------------------------------

def _pick_recent_uploads(data_dir: Path, max_uploads: int) -> list[Path]:
    """Return the most-recent N ``*_cog.tif`` files in ``data_dir`` by mtime.

    The imagery pipeline writes one ``<upload-id>_<basename>_cog.tif`` per
    completed upload (see ``backend/worker_legacy.py#L4506``), so each COG
    file is treated as one logical upload here.
    """
    if not data_dir.exists() or not data_dir.is_dir():
        return []
    cog_files = [p for p in data_dir.glob("*_cog.tif") if p.is_file()]
    cog_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cog_files[:max_uploads]


def _upload_id_from_cog(cog_path: Path) -> str:
    """Extract the upload-id prefix from ``<upload-id>_<basename>_cog.tif``.

    Falls back to the full stem when the conventional ``_`` separator is
    missing so the script never crashes on hand-placed files.
    """
    name = cog_path.stem  # drops .tif
    if name.endswith("_cog"):
        name = name[:-4]
    head, _, _ = name.partition("_")
    return head or cog_path.stem


def _extract_chips_from_cog(
    cog_path: Path, chips_per_upload: int
) -> Iterable[tuple[int, "numpy.ndarray", dict]]:
    """Yield ``(chip_idx, rgb_uint8_array, metadata_dict)`` for one COG.

    Picks ``chips_per_upload`` evenly-spaced windows across the raster. The
    window size is the smaller of 1008 (matching ``INFERENCE_CHIP_SIZE``) or
    the raster's own size for small inputs.

    Uses the same percentile-stretch that ``worker_legacy.chip_to_uint8_rgb``
    uses so triage chips look like what the detector actually sees.
    """
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    with rasterio.open(cog_path) as src:
        width = src.width
        height = src.height
        chip_size = min(1008, width, height)

        # Even spacing across the larger dimension, top-left corners.
        # When the raster is too small for ``chips_per_upload`` non-overlapping
        # windows we still emit the requested number (with overlap) so the
        # analyst gets the chip count they asked for.
        if chips_per_upload <= 0:
            return
        if chips_per_upload == 1:
            origins = [(0, 0)]
        elif width <= chip_size:
            # Raster too small for distinct windows along the X axis; emit a
            # single chip rather than N identical duplicates.
            log.info(
                "raster %s too small for %d distinct chips; emitting 1",
                cog_path.name, chips_per_upload,
            )
            origins = [(0, 0)]
        else:
            stride_x = max(1, (width - chip_size) // max(1, chips_per_upload - 1))
            origins = [
                (min(width - chip_size, i * stride_x), 0)
                for i in range(chips_per_upload)
            ]

        for idx, (col_off, row_off) in enumerate(origins):
            window = Window(col_off, row_off, chip_size, chip_size)
            chip = src.read(window=window, boundless=True, fill_value=0)
            rgb = _chip_to_uint8_rgb(chip, np)
            meta = {
                "modality": "rgb",
                "sensor": "optical",
                "branch": "default",
                "source_cog": cog_path.name,
                "window": {"col_off": col_off, "row_off": row_off,
                           "width": chip_size, "height": chip_size},
                "width": int(rgb.shape[1]),
                "height": int(rgb.shape[0]),
            }
            yield idx, rgb, meta


def _chip_to_uint8_rgb(chip, np):
    """Same percentile-stretch as ``worker_legacy.chip_to_uint8_rgb``.

    Re-implemented here (instead of imported) so the script stays usable
    outside the backend container, where ``backend.worker_legacy`` cannot be
    imported without its full dependency tree.
    """
    chip_rgb = chip[:3] if chip.shape[0] >= 3 else np.repeat(chip[:1], 3, axis=0)
    chip_rgb = np.nan_to_num(chip_rgb.astype("float32"), nan=0.0, posinf=0.0, neginf=0.0)
    if chip_rgb.dtype != np.uint8:
        low, high = np.percentile(chip_rgb, [2, 98])
        if high > low:
            chip_rgb = np.clip((chip_rgb - low) / (high - low) * 255, 0, 255).astype(np.uint8)
        else:
            chip_rgb = np.zeros_like(chip_rgb, dtype=np.uint8)
    return np.moveaxis(chip_rgb, 0, -1)


# ---------------------------------------------------------------------------
# Discovery — api mode
# ---------------------------------------------------------------------------

def _pick_recent_uploads_via_api(
    api_url: str, session_cookie: str | None, max_uploads: int
) -> list[dict]:
    """Hit ``GET /api/ingest/uploads`` and return the most recent N rows.

    The endpoint already orders by ``updated_at DESC`` so we just slice.
    """
    import requests

    if not session_cookie:
        raise RuntimeError(
            "api mode requires --session-cookie or $SENTINEL_SESSION_COOKIE"
        )
    resp = requests.get(
        f"{api_url.rstrip('/')}/api/ingest/uploads",
        cookies={"session": session_cookie},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("uploads", []) or []
    return rows[:max_uploads]


# ---------------------------------------------------------------------------
# Write outputs
# ---------------------------------------------------------------------------

_README_TEMPLATE = """# Triage Set — {date}

This directory holds an analyst-curated benchmark used by
`scripts/compare_inference_layers.py --triage-set <this-dir>` to measure
detection quality on the operator's own recent uploads.

## How to annotate

1. Open `annotations.yaml`.
2. For each chip row, fill in `expected_labels` with the ontology labels you
   expect the detector to find on that chip.
   - Use canonical ontology labels (see the Ontology Admin UI for the full
     vocabulary, or `/api/ontology/default-prompts`).
   - Leave `expected_labels: []` for chips where nothing should be detected
     (those rows still run and are used to measure false-positive rate).
3. Save the file.

## How to score

```bash
python scripts/compare_inference_layers.py \\
  --triage-set {out_dir} \\
  --url http://localhost:8001 \\
  --output bench/triage/{date}/before.md \\
  --json-output bench/triage/{date}/before.json
```

Re-run after each detection-quality change and diff the per-class metrics.
"""


def _normalise_sources(
    sources: list[tuple[Path | dict, str]],
) -> list[tuple[Path, str, dict]]:
    """Normalise mixed (data-dir/api) sources to ``(cog_path, upload_id, extra_meta)``.

    Both source modes ultimately resolve to a COG path on disk plus an
    upload_id; api rows also contribute an ``upload_row`` extra that is merged
    into each chip's sidecar metadata. Collapsing them here lets the chip
    write loop stay branch-free.

    Drops api rows whose ``file_path`` is missing or not on disk (with a
    warning). Drops later collisions when two COGs resolve to the same
    upload_id — the chip filenames would otherwise overwrite silently.
    """
    normalised: list[tuple[Path, str, dict]] = []
    seen_ids: set[str] = set()
    for source, mode in sources:
        if mode == "data-dir":
            cog_path: Path = source  # type: ignore[assignment]
            upload_id = _upload_id_from_cog(cog_path)
            extra_meta: dict = {}
        elif mode == "api":
            row: dict = source  # type: ignore[assignment]
            cog_path_str = (row.get("file_path") or "").strip()
            if not cog_path_str:
                log.warning("api row missing file_path; skipping: %s", row.get("upload_id"))
                continue
            cog_path = Path(cog_path_str)
            if not cog_path.exists():
                log.warning("api row file_path not on disk; skipping: %s", cog_path)
                continue
            upload_id = row.get("upload_id") or _upload_id_from_cog(cog_path)
            extra_meta = {
                "upload_row": {
                    "upload_id": row.get("upload_id"),
                    "filename": row.get("filename"),
                    "status": row.get("status"),
                }
            }
        else:
            log.warning("unknown source mode %r; skipping", mode)
            continue

        if upload_id in seen_ids:
            log.warning(
                "upload_id %s collides with previous; skipping %s",
                upload_id, cog_path,
            )
            continue
        seen_ids.add(upload_id)
        normalised.append((cog_path, upload_id, extra_meta))

    return normalised


def _write_triage_set(
    out_dir: Path,
    sources: list[tuple[Path | dict, str]],
    chips_per_upload: int,
    dry_run: bool,
) -> int:
    """Materialise chips and annotations.yaml. Returns the number of chips written."""
    chips_dir = out_dir / "chips"
    yaml_rows: list[dict] = []
    chips_written = 0

    for cog_path, upload_id, extra_meta in _normalise_sources(sources):
        for chip_idx, rgb, meta in _extract_chips_from_cog(cog_path, chips_per_upload):
            if extra_meta:
                meta.update(extra_meta)
            chip_name = f"{upload_id}_{chip_idx}.png"
            yaml_rows.append({
                "chip": chip_name,
                "sensor": meta["sensor"],
                "expected_labels": [],
            })
            if not dry_run:
                _write_png(chips_dir / chip_name, rgb)
                (chips_dir / f"{upload_id}_{chip_idx}.json").write_text(
                    json.dumps(meta, indent=2)
                )
            chips_written += 1

    if dry_run:
        log.info(
            "DRY RUN: would write %d chip(s) (and matching JSON sidecars) to %s",
            chips_written, chips_dir,
        )
        log.info(
            "DRY RUN: would write annotations.yaml and README.md to %s",
            out_dir,
        )
        return chips_written

    import yaml

    out_dir.mkdir(parents=True, exist_ok=True)
    chips_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "annotations.yaml").write_text(
        "# Fill in `expected_labels` for each chip. Use canonical ontology labels.\n"
        "# Leave `expected_labels: []` for chips where nothing is expected.\n"
        + yaml.safe_dump({"chips": yaml_rows}, sort_keys=False)
    )

    today = datetime.now(tz=timezone.utc).date().isoformat()
    (out_dir / "README.md").write_text(
        _README_TEMPLATE.format(date=today, out_dir=str(out_dir))
    )

    return chips_written


def _write_png(path: Path, rgb_array) -> None:
    """Write an RGB uint8 numpy array to ``path`` as PNG (Pillow required)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb_array).save(path, format="PNG")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_out_dir() -> Path:
    today = datetime.now(tz=timezone.utc).date().isoformat()
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "bench" / "triage" / today


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a triage benchmark from recent ingested imagery.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Output directory for the triage set "
            "(default: bench/triage/<today-utc>)."
        ),
    )
    parser.add_argument(
        "--max-uploads",
        type=int,
        default=50,
        dest="max_uploads",
        help="How many recent uploads to consider (default: 50).",
    )
    parser.add_argument(
        "--chips-per-upload",
        type=int,
        default=2,
        dest="chips_per_upload",
        help="Chips to extract per upload (default: 2).",
    )
    parser.add_argument(
        "--source",
        choices=["data-dir", "api"],
        default="data-dir",
        help=(
            "Where to discover recent uploads. 'data-dir' scans a mounted "
            "/data/imagery/processed directory directly (no auth required). "
            "'api' calls GET /api/ingest/uploads with a session cookie."
        ),
    )
    parser.add_argument(
        "--data-dir",
        default="/data/imagery/processed",
        dest="data_dir",
        help="Path scanned in --source data-dir mode (default: /data/imagery/processed).",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:3000",
        dest="api_url",
        help="Backend base URL for --source api mode (default: http://localhost:3000).",
    )
    parser.add_argument(
        "--session-cookie",
        default=None,
        dest="session_cookie",
        help=(
            "Signed session cookie for --source api mode. Falls back to the "
            "SENTINEL_SESSION_COOKIE env var when unset."
        ),
    )
    parser.add_argument(
        "--rgb-only",
        action="store_true",
        default=True,
        dest="rgb_only",
        help="Restrict chips to RGB modality (default: True).",
    )
    parser.add_argument(
        "--include-non-rgb",
        action="store_false",
        dest="rgb_only",
        help="Disable the RGB-only filter; include SAR / multispectral chips too.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Print what would be written without touching disk.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    out_dir = Path(args.out) if args.out else _default_out_dir()
    dry_run: bool = args.dry_run

    log.info(
        "source=%s out=%s max_uploads=%d chips_per_upload=%d dry_run=%s",
        args.source, out_dir, args.max_uploads, args.chips_per_upload, dry_run,
    )

    sources: list[tuple[Path | dict, str]] = []

    if args.source == "data-dir":
        data_dir = Path(args.data_dir)
        cogs = _pick_recent_uploads(data_dir, args.max_uploads)
        if not cogs:
            msg = (
                f"No *_cog.tif files found under {data_dir} — nothing to write. "
                "Either ingest some imagery first or pass --source api."
            )
            log.warning(msg)
            print(msg)
            return 0
        sources = [(p, "data-dir") for p in cogs]
        log.info("Discovered %d recent upload(s) under %s", len(cogs), data_dir)
    else:
        cookie = args.session_cookie or os.environ.get("SENTINEL_SESSION_COOKIE")
        rows = _pick_recent_uploads_via_api(args.api_url, cookie, args.max_uploads)
        if not rows:
            log.warning("API returned no uploads — nothing to write.")
            return 0
        sources = [(row, "api") for row in rows]
        log.info("Fetched %d recent upload(s) from %s", len(rows), args.api_url)

    chips_written = _write_triage_set(
        out_dir=out_dir,
        sources=sources,
        chips_per_upload=args.chips_per_upload,
        dry_run=dry_run,
    )

    if dry_run:
        print(
            f"DRY RUN: would write {chips_written} chip(s) to {out_dir}; "
            f"would also emit annotations.yaml and README.md."
        )
    else:
        print(
            f"Wrote {chips_written} chips to {out_dir}; "
            f"edit annotations.yaml then run "
            f"compare_inference_layers.py --triage-set {out_dir}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
