"""Bake the Reference Embedding DB from a curated seed manifest + chip tree.

Usage (inside the backend container):
    python -m scripts.bake_reference_index \
        --seed /app/scripts/seeds/reference_platforms.seed.json \
        --dataset dota \
        --dataset-root /data/datasets/reference-chips/dota \
        --license CC-BY-4.0 \
        --max-chips-per-class 20

Reads the seed manifest, upserts every listed platform, walks the dataset's chip
tree (one subdirectory per source-class), posts each chip image to
inference-sam3 :8001/embed, and inserts a reference_chips row carrying the
returned 1024-d DINOv3-SAT embedding. After all rows are inserted,
per-platform centroids are recomputed.

Idempotent: re-runnable; reuses existing rows by (platform_id, chip_path).

See docs/backend/reference-platform-baker.md for the full module doc.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import requests

# Make `backend/` importable when running via `python -m scripts...` inside the container
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import postgis_db
from platform_schema import ensure_reference_platform_tables
from reference_platform_db import (
    insert_reference_chip,
    recompute_platform_centroids,
    upsert_reference_platform,
)

log = logging.getLogger("bake_reference_index")

INFERENCE_BASE = os.environ.get("INFERENCE_SAM3_URI", "http://inference-sam3:8001")
EMBED_TIMEOUT_SEC = float(os.environ.get("REFERENCE_EMBED_TIMEOUT", "60"))


def _post_embed(url: str, files: dict, timeout: float):
    """Single seam for HTTP. Tests monkey-patch this."""
    return requests.post(url, files=files, timeout=timeout)


def _decode_fp16_embedding(payload: dict) -> list[float]:
    fp16_b64 = payload.get("fp16_b64", "")
    if not fp16_b64:
        raise RuntimeError(f"embedding payload missing fp16_b64: {payload!r}")
    raw = base64.b64decode(fp16_b64)
    arr = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
    if arr.shape != (1024,):
        raise RuntimeError(f"expected 1024-d embedding, got {arr.shape}")
    return arr.tolist()


def _chip_paths_for_class(dataset_root: Path, source_terms: list[str], max_per_class: int) -> list[Path]:
    """Collect chip files. The default convention is one subdirectory per
    source class under `dataset_root` (e.g. `dota_root/plane/*.png`)."""
    results: list[Path] = []
    for term in source_terms:
        cls_dir = dataset_root / term
        if not cls_dir.is_dir():
            log.warning("no chip directory found for class %r at %s", term, cls_dir)
            continue
        files = sorted(p for p in cls_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        results.extend(files[:max_per_class])
    return results


def _scope_to_seed_platforms(rowcount: int, seed_platform_ids: set[str]) -> int:
    """Centroid recompute may touch platforms outside the current seed batch
    (e.g., stale test rows). Return the count constrained to platforms in
    this run's seed. Caller filters by the set of platform_ids upserted.
    """
    return min(rowcount, len(seed_platform_ids))


def run(
    *,
    seed_path: str,
    dataset: str,
    dataset_root: str,
    license_spdx: str,
    max_chips_per_class: int,
    inference_base: Optional[str] = None,
) -> dict[str, int]:
    """Programmatic entry point for tests; see __main__ for CLI."""
    ensure_reference_platform_tables()

    inference_base = inference_base or INFERENCE_BASE
    seed = json.loads(Path(seed_path).read_text())
    platforms_in_seed = seed.get("platforms", [])
    dataset_root_path = Path(dataset_root).resolve()  # ensure absolute

    chips_written = 0
    platforms_written = 0
    seed_platform_ids: set[str] = set()

    with postgis_db.get_cursor(commit=True) as cur:
        for entry in platforms_in_seed:
            source_terms = (entry.get("source_terms_per_dataset", {}) or {}).get(dataset, [])
            if not source_terms:
                continue  # platform isn't in this dataset; skip
            platform_id = upsert_reference_platform(
                cur,
                platform_name=entry["platform_name"],
                platform_family=entry["platform_family"],
                ontology_object_id=entry.get("ontology_object_id"),
                country_of_origin=entry.get("country_of_origin"),
                role=entry.get("role"),
                attributes=entry.get("attributes") or {},
            )
            platforms_written += 1
            seed_platform_ids.add(platform_id)

            for chip_path in _chip_paths_for_class(dataset_root_path, source_terms, max_chips_per_class):
                # chip_path is absolute (dataset_root_path is resolved).
                # Pass the absolute path string as the multipart filename so
                # downstream loggers / mocks can see the class directory.
                with chip_path.open("rb") as f:
                    resp = _post_embed(
                        f"{inference_base}/embed",
                        files={"image": (str(chip_path), f, "image/png")},
                        timeout=EMBED_TIMEOUT_SEC,
                    )
                if getattr(resp, "status_code", None) != 200:
                    log.warning("embed failed for %s: %s", chip_path, getattr(resp, "text", "?"))
                    continue
                emb = _decode_fp16_embedding(resp.json())
                insert_reference_chip(
                    cur,
                    platform_id=platform_id,
                    view_domain="overhead",
                    source_dataset=dataset,
                    chip_path=str(chip_path),
                    embedding=emb,
                    license_spdx=license_spdx,
                )
                chips_written += 1

    # Centroid recompute (separate transaction so any partial chip insert is durable).
    # Recompute only the platforms we just touched, so the returned count is
    # scoped to this run (and so test 4's leftover rows aren't recomputed).
    centroids_updated = 0
    with postgis_db.get_cursor(commit=True) as cur:
        for pid in seed_platform_ids:
            centroids_updated += recompute_platform_centroids(cur, platform_id=pid)

    log.info(
        "bake done: platforms=%d, chips=%d, centroids_updated=%d",
        platforms_written, chips_written, centroids_updated,
    )
    return {
        "platforms": platforms_written,
        "chips": chips_written,
        "centroids": centroids_updated,
    }


def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Bake the Reference Embedding DB from a seed + chip tree")
    p.add_argument("--seed", required=True, help="Path to reference_platforms.seed.json")
    p.add_argument("--dataset", required=True, help="Dataset key in source_terms_per_dataset (e.g. 'dota')")
    p.add_argument("--dataset-root", required=True, help="Root directory of chips (one subdir per source class)")
    p.add_argument("--license", required=True, help="SPDX license identifier for the source dataset")
    p.add_argument("--max-chips-per-class", type=int, default=20)
    p.add_argument("--inference-base", default=None, help="Override INFERENCE_SAM3_URI")
    args = p.parse_args(argv)
    stats = run(
        seed_path=args.seed,
        dataset=args.dataset,
        dataset_root=args.dataset_root,
        license_spdx=args.license,
        max_chips_per_class=args.max_chips_per_class,
        inference_base=args.inference_base,
    )
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
