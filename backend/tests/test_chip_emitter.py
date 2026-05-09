from __future__ import annotations

import sys
import importlib
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _write_tif(path: Path, data: np.ndarray, descriptions: tuple[str | None, ...] = ()):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[1],
        width=data.shape[2],
        count=data.shape[0],
        dtype=str(data.dtype),
        crs="EPSG:32640",
        transform=from_origin(703000, 2770000, 0.6, 0.6),
    ) as dst:
        dst.write(data)
        for index, desc in enumerate(descriptions, start=1):
            if desc:
                dst.set_band_description(index, desc)


def test_emit_chip_payload_multispectral_for_sam3(tmp_path):
    import worker

    path = tmp_path / "hls.tif"
    _write_tif(path, np.ones((6, 8, 8), dtype=np.float32))
    with rasterio.open(path) as src:
        chip_file, meta = worker._emit_chip_payload(Window(0, 0, 8, 8), src, valid_mask=None)

    assert meta["content_type"] == "image/tiff"
    assert meta["modality"] == "multispectral"
    assert meta["geo"]["source_crs"] == "EPSG:32640"
    chip_file.close()


def test_emit_chip_payload_sar_for_sam3(tmp_path):
    import worker

    path = tmp_path / "sar.tif"
    _write_tif(path, np.ones((2, 8, 8), dtype=np.float32), ("VV", "VH"))
    with rasterio.open(path) as src:
        chip_file, meta = worker._emit_chip_payload(Window(0, 0, 8, 8), src, valid_mask=None)

    assert meta["content_type"] == "image/tiff"
    assert meta["modality"] == "sar"
    assert meta["sar_polarizations"] == ["VV", "VH"]
    chip_file.close()


def test_fast_review_profile_defaults(monkeypatch):
    monkeypatch.setenv("INFERENCE_SPEED_PROFILE", "fast_review")
    monkeypatch.delenv("INFERENCE_CHIP_SIZE", raising=False)
    monkeypatch.delenv("INFERENCE_CHIP_OVERLAP", raising=False)
    monkeypatch.delenv("MAX_INFERENCE_CHIPS", raising=False)
    monkeypatch.delenv("INFERENCE_CHIP_CONCURRENCY", raising=False)

    import worker

    worker = importlib.reload(worker)

    assert worker.INFERENCE_SPEED_PROFILE == "fast_review"
    assert worker.DEFAULT_INFERENCE_CHIP_SIZE == 1008
    assert worker.DEFAULT_INFERENCE_OVERLAP == 252
    assert worker.MAX_INFERENCE_CHIPS == 256
    assert worker.INFERENCE_CHIP_CONCURRENCY == 1


def test_recall_review_profile_keeps_full_coverage(monkeypatch):
    monkeypatch.setenv("INFERENCE_SPEED_PROFILE", "recall_review")
    monkeypatch.delenv("INFERENCE_CHIP_SIZE", raising=False)
    monkeypatch.delenv("INFERENCE_CHIP_OVERLAP", raising=False)
    monkeypatch.delenv("MAX_INFERENCE_CHIPS", raising=False)
    monkeypatch.delenv("INFERENCE_CHIP_CONCURRENCY", raising=False)

    import worker

    worker = importlib.reload(worker)

    assert worker.INFERENCE_SPEED_PROFILE == "recall_review"
    assert worker.DEFAULT_INFERENCE_CHIP_SIZE == 1008
    assert worker.DEFAULT_INFERENCE_OVERLAP == 252
    assert worker.MAX_INFERENCE_CHIPS == 0
    assert worker.INFERENCE_CHIP_CONCURRENCY == 2
