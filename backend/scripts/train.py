#!/usr/bin/env python3
"""Thin training-job orchestrator invoked by the Celery worker.

Reads a row from ``training_jobs``, POSTs to inference-sam3 ``/train``, polls
``/train/{job_id}`` every ``TRAIN_POLL_INTERVAL_S`` seconds, writes status +
metrics back to PostGIS, and on success inserts a new row into ``models``.

CLI: ``python -m scripts.train --job <id> --dataset <path> --epochs <int> --out <dir>``

Exits 0 on success, non-zero on failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import postgis_db  # noqa: E402

INFERENCE_SAM3_URL = os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001")
TRAIN_POLL_INTERVAL_S = float(os.getenv("TRAIN_POLL_INTERVAL_S", "30"))
TRAIN_MAX_WAIT_S = float(os.getenv("TRAIN_MAX_WAIT_S", "86400"))  # 24 h

logger = logging.getLogger("scripts.train")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _update_job(job_id: int, status: str | None = None, metrics: dict | None = None) -> None:
    parts = []
    args: list = []
    if status is not None:
        parts.append("status = %s")
        args.append(status)
    if metrics:
        parts.append("metrics = coalesce(metrics, '{}'::jsonb) || %s::jsonb")
        args.append(json.dumps(metrics, default=str))
    if not parts:
        return
    parts.append("updated_at = NOW()")
    args.append(job_id)
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(f"UPDATE training_jobs SET {', '.join(parts)} WHERE id = %s", args)


def _register_model(name: str, weights_path: str, metrics: dict) -> int | None:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO models (name, version, model_path, status, metrics, promoted)
            VALUES (%s, %s, %s, 'available', %s::jsonb, FALSE)
            RETURNING id
            """,
            (
                name[:255],
                f"local-{int(time.time())}",
                weights_path,
                json.dumps(metrics or {}, default=str),
            ),
        )
        row = cur.fetchone()
    return int(row["id"]) if row else None


def _maybe_tile_dataset(dataset_path: str, out_dir: str, chip_size: int, overlap: int) -> str:
    """Opt-in chip-aligned preprocessing. Tiles the dataset with the SAME
    planner inference uses so train/inference pixel distributions match, then
    returns the tiled data.yaml path. Images already <= chip_size pass through
    as single tiles, so this is safe (a no-op in geometry) for 640 px datasets
    like MVRSD while still being correct for larger user imagery.

    Runs in the worker venv (CPU libs only): PIL + the pure-Python planner.
    """
    src = Path(dataset_path)
    src_root = src.parent if src.is_file() else src  # accept data.yaml or dir
    tiled_root = Path(out_dir) / "tiled-dataset"
    # Lazy import — keeps the worker import-light and avoids a hard dep when
    # tiling isn't requested.
    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    try:
        from prepare_training_tiles import tile_dataset
    finally:
        sys.path.pop(0)
    summary = tile_dataset(src_root, tiled_root, chip_size=chip_size, overlap=overlap)
    logger.info("chip-aligned tiling: %s", json.dumps(summary, default=str))
    return str(tiled_root / "data.yaml")


def run(job_id: int, dataset_path: str, epochs: int, out_dir: str,
        tile: bool = False, chip_size: int = 1008, overlap: int = 252) -> int:
    if not dataset_path:
        logger.error("dataset_path is empty")
        _update_job(job_id, status="failed", metrics={"error": "dataset_path empty"})
        return 2

    Path(out_dir).mkdir(parents=True, exist_ok=True)

    if tile:
        try:
            dataset_path = _maybe_tile_dataset(dataset_path, out_dir, chip_size, overlap)
            _update_job(job_id, metrics={"tiled_dataset_path": dataset_path,
                                         "tile_chip_size": chip_size, "tile_overlap": overlap})
        except Exception as exc:  # noqa: BLE001
            logger.exception("chip-aligned tiling failed")
            _update_job(job_id, status="failed", metrics={"error": f"tiling failed: {exc}"})
            return 8

    with postgis_db.get_cursor() as cur:
        cur.execute("SELECT id, name FROM training_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    if not row:
        logger.error("training_jobs row %s not found", job_id)
        return 3
    name = (row.get("name") if isinstance(row, dict) else row[1]) or f"job-{job_id}"

    try:
        response = requests.post(
            f"{INFERENCE_SAM3_URL}/train",
            json={"name": name, "dataset_path": dataset_path, "epochs": epochs},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("POST /train failed")
        _update_job(job_id, status="failed", metrics={"error": str(exc)})
        return 4

    body = response.json()
    remote_job_id = body.get("job_id")
    if not remote_job_id:
        _update_job(job_id, status="failed", metrics={"error": f"no job_id in response: {body}"})
        return 5

    _update_job(job_id, status="running", metrics={"remote_job_id": remote_job_id})

    deadline = time.time() + TRAIN_MAX_WAIT_S
    while time.time() < deadline:
        time.sleep(TRAIN_POLL_INTERVAL_S)
        try:
            poll = requests.get(f"{INFERENCE_SAM3_URL}/train/{remote_job_id}", timeout=10)
            poll.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("poll error (will retry): %s", exc)
            continue
        status_body = poll.json()
        status = status_body.get("status")
        _update_job(job_id, metrics={"poll": status_body})
        if status == "done":
            weights_path = status_body.get("weights_path")
            metrics = status_body.get("metrics") or {}
            model_id = _register_model(name, weights_path or "", metrics)
            _update_job(job_id, status="done", metrics={"weights_path": weights_path, "model_id": model_id, "metrics": metrics})
            logger.info("training job %s complete; model_id=%s", job_id, model_id)
            return 0
        if status == "failed":
            err = status_body.get("error") or "unknown"
            _update_job(job_id, status="failed", metrics={"error": err})
            logger.error("training job %s failed: %s", job_id, err)
            return 6

    _update_job(job_id, status="failed", metrics={"error": f"timeout after {TRAIN_MAX_WAIT_S}s"})
    return 7


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=int, required=True)
    parser.add_argument("--dataset", type=str, required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--tile", action="store_true",
                        help="chip-align the dataset with plan_inference_grid before training")
    parser.add_argument("--chip-size", type=int, default=1008)
    parser.add_argument("--overlap", type=int, default=252)
    args = parser.parse_args()
    return run(args.job, args.dataset, args.epochs, args.out,
               tile=args.tile, chip_size=args.chip_size, overlap=args.overlap)


if __name__ == "__main__":
    sys.exit(main())
